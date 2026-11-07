import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from app.api import health
from app.api.base_v1.api import router as api_v1_router
from app.api.base_v1.endpoints.identity import discovery as identity_discovery
from app.api.scim import users as scim_users
from app.config import settings
from app.middlewares.audit import AuditMiddleware
from app.middlewares.body_size_limit import BodySizeLimitMiddleware
from app.middlewares.disable_cache_middleware import DisableCacheMiddleware
from app.middlewares.metrics import MetricsMiddleware
from app.middlewares.timing_middleware import TimingMiddleware
from app.monitoring import BUILD_INFO
from app.monitoring.clock_skew import ClockSkew
from app.monitoring.router import router as metrics_router
from app.observability.log_format import configure as configure_logging
from app.observability.otel import ServerTelemetry
from app.security.startup_checks import (
    check_deployment,
    check_no_bypass_identity_in_database,
    check_pool_fits_the_server,
)
from app.services.ai.pool_manager import PoolManager
from app.services.cloud.producer_feed import ProducerFeed
from app.services.deployment_licence import LicenceDailyAudit
from app.services.identity.extension_assets import ASSET_UPLOAD_PATH, MAX_ASSET_BYTES
from app.services.identity.extensions import EXTENSION_UPLOAD_PATH, MAX_ARCHIVE_BYTES
from app.services.identity.key_ring import SigningKeys
from app.services.identity.organisation_deletions import OrganisationDeletions
from app.services.retention import Retention

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Before anything else, and deliberately NOT inside a try: a
    # deployment that cannot authenticate anybody must fail to start
    # rather than serve. Everything below is best-effort init that
    # degrades; this is not.
    check_deployment()

    # Also not inside a try, and for the same reason. The dev bypass
    # writes a ``demo-user-001`` admin row that outlives the flag, so a
    # database it has touched carries a live administrator account with an
    # empty password. See the check's own docstring.
    await check_no_bypass_identity_in_database()

    # Not inside a try either: a server that cannot sign cannot sign
    # anybody in. Generates the deployment's first key on first start.
    await SigningKeys.load()
    key_reload = asyncio.create_task(SigningKeys.reload_forever(), name='signing-key-reload')

    # A warning, not a gate: how many replicas share the server is not
    # something one process knows. It states both numbers in the log
    # where a deploy can see them. F116.
    await check_pool_fits_the_server()

    # Retention (DP-6): a setting is not a limit, so something has to
    # apply it. Does nothing and starts nothing unless a window is
    # configured — both default to 0, meaning keep everything.
    retention = Retention()
    retention.start()

    # A deleted organisation reaches every service, through the outbox
    # its deletion wrote. Unconditional: a service down at the time of a
    # deletion is told when it is back. See docs/service-identity.md §6.1.
    organisation_deletions = OrganisationDeletions()
    organisation_deletions.start()

    # The producers feed carries every producer's effective VM profile,
    # clamped to the caps; the deployment's own caps change only with a
    # redeploy, which no trigger sees. Background: the feed catches up.
    feed_started = asyncio.create_task(ProducerFeed().started_in_background(), name='producer-feed-started')

    # The paid modules' licence: once a day while one is expiring, lapsed
    # or read-only, the deployment's audit trail says so. igni server
    # only reports the licence; each module enforces it itself.
    licence_daily_audit = LicenceDailyAudit()
    licence_daily_audit.start()

    # Clock skew (B8). Session revocation, the audit trail's ordering and
    # retention windows all compare this process's clock with the
    # database's, and all fail quietly when the two disagree.
    # Unconditional, because a deployment with nothing else on still has
    # clocks. Sampled once here so a bad clock is in the boot log.
    clock_skew = ClockSkew()
    await clock_skew.sample_once()
    clock_skew.start()

    # The inference nodes chat is routed across. Best-effort: a pool that
    # cannot summarise itself degrades chat, not the whole server.
    try:
        await PoolManager().get_pool().log_pool_summary()
    except Exception:  # noqa: BLE001 — best-effort startup diagnostics
        logger.warning('Inference pool init failed', exc_info=True)

    yield

    try:
        await PoolManager().close()
    except Exception:  # noqa: BLE001 — shutdown is not a place to raise
        # These pools hold upstream connections, and a close that failed
        # is the only warning anybody gets that a rolling restart is
        # leaving them behind.
        logger.exception('Could not close the AI key pools')
    await retention.stop()
    await organisation_deletions.stop()
    await licence_daily_audit.stop()
    key_reload.cancel()
    feed_started.cancel()
    await clock_skew.stop()
    # The traces still held go to the collector before the process ends.
    ServerTelemetry.flush()

    logger.info('Application shutdown complete')


def create_app() -> FastAPI:
    """The application: logging, routes and middleware, in the order they must be applied."""
    # `LOG_FORMAT=json` makes every line an object a log shipper can filter
    # on. See app/observability/log_format.py.
    log_level = settings.log_level.upper()
    configure_logging(log_level, as_json=settings.log_format.lower() == 'json')
    logger.info('Application starting with log level: %s', log_level)

    show_docs = settings.app_env == 'dev'
    application = FastAPI(
        title=settings.app_name,
        root_path=settings.app_root_path,
        version=settings.app_version,
        lifespan=lifespan,
        docs_url='/docs' if show_docs else None,
        redoc_url='/redoc' if show_docs else None,
        openapi_url='/openapi.json' if show_docs else None,
    )

    application.include_router(health.router)
    application.include_router(api_v1_router, prefix='/v1')
    application.include_router(identity_discovery.router)

    # SCIM 2.0, at the path the standard fixes. Not under `/v1`: a
    # provider is configured with a base URL and appends `/Users` itself,
    # so the shape is not ours to version. Answers 503 throughout unless
    # SCIM_TOKEN is set, so a deployment that has not configured
    # provisioning is not running an unauthenticated one.
    application.include_router(scim_users.router, prefix='/scim/v2', tags=['SCIM'])

    # `GET /metrics`, 404 unless METRICS_TOKEN is set. Not under /v1: it is
    # not part of the product's API and is not versioned with it.
    application.include_router(metrics_router)

    # Always 1; the label is the payload. Set at startup so a replica
    # reports its version before it has served anything, since the alert
    # this feeds is about two replicas disagreeing during a rollout.
    BUILD_INFO.labels(version=settings.app_version).set(1)

    # ``add_middleware`` wraps outward: the first registered runs closest
    # to the endpoint, the last registered is the outermost.
    #
    # Innermost on purpose: it needs the resolved route, its path
    # parameters and the actor that the auth dependency stashed on
    # ``request.state``, none of which exist further out.
    application.add_middleware(AuditMiddleware)

    # Further out than the audit middleware: the audit trail records what a
    # user did on the routes F90's policy covers, while a 500 is worth
    # counting wherever it happens.
    application.add_middleware(MetricsMiddleware)

    application.add_middleware(TimingMiddleware)
    application.add_middleware(DisableCacheMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_credentials=False,
        allow_methods=['*'],
        allow_headers=['*'],
        expose_headers=['*'],
    )

    # Outermost: an oversized body is refused before the timing, cache and
    # CORS layers touch it, and before FastAPI parses it into Python objects.
    application.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=settings.max_request_body_bytes,
        larger={EXTENSION_UPLOAD_PATH: MAX_ARCHIVE_BYTES, ASSET_UPLOAD_PATH: MAX_ASSET_BYTES},
    )

    # OpenTelemetry traces, to the operator's own collector — only when
    # OTEL_EXPORTER_OTLP_ENDPOINT names one; with none nothing is built.
    # After the middleware above, so the request span is the outermost
    # thing a request passes through. docs/observability.md §5.
    ServerTelemetry.start(application, settings.otel)

    return application


app = create_app()


if __name__ == '__main__':
    # Binding all interfaces is required inside a container; what is
    # exposed is decided by the compose port mapping, not by this line.
    # This block is the local-dev entrypoint — the image runs uvicorn from
    # its CMD and never reaches here.
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', '7777')))  # nosec B104
