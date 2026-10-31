import asyncio
from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from app.api.base_v1.api import router as api_v1_router
from app.api.base_v1.endpoints.identity import discovery as identity_discovery
from app.api.scim import users as scim_users
from app.config import settings
from app.middlewares.audit import AuditMiddleware
from app.middlewares.body_size_limit import BodySizeLimitMiddleware
from app.middlewares.disable_cache_middleware import DisableCacheMiddleware
from app.middlewares.metrics import MetricsMiddleware
from app.middlewares.timing_middleware import TimingMiddleware
from app.monitoring import BUILD_INFO, clock_skew
from app.monitoring.router import router as metrics_router
from app.observability.log_format import configure as configure_logging
from app.observability.otel import ServerTelemetry
from app.security.startup_checks import (
    check_deployment,
    check_no_bypass_identity_in_database,
    check_pool_fits_the_server,
)
from app.services import retention
from app.services.ai.pool_manager import PoolManager
from app.services.cloud.producer_feed import ProducerFeed
from app.services.deployment_licence import LicenceDailyAudit
from app.services.identity.extension_assets import ASSET_UPLOAD_PATH, MAX_ASSET_BYTES
from app.services.identity.extensions import EXTENSION_UPLOAD_PATH, MAX_ARCHIVE_BYTES
from app.services.identity.key_ring import SigningKeys
from app.services.identity.organisation_deletions import OrganisationDeletions
from app.services.readiness import LivenessReport, ReadinessProbe, ReadinessReport, VersionReport

# Configure logging. `LOG_FORMAT=json` makes every line an object a log
# shipper can filter on, which is the difference between a customer being
# able to query their logs and having to write regular expressions. See
# app/observability/log_format.py.
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
configure_logging(LOG_LEVEL, as_json=os.getenv('LOG_FORMAT', 'text').lower() == 'json')

logger = logging.getLogger(__name__)
logger.info(f'🚀 Application starting with log level: {LOG_LEVEL}')


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Before anything else, and deliberately NOT inside a try: a
    # deployment that cannot authenticate anybody must fail to start
    # rather than serve. Everything below is best-effort init that
    # degrades; this is not.
    check_deployment()

    # Also not inside a try, and for the same reason. The dev bypass does
    # not only skip authentication — it writes a ``demo-user-001`` admin
    # row that outlives the flag, so a database it has touched carries a
    # live administrator account with an empty password. Proven takeover
    # against a production-mode server; see the check's own comment.
    await check_no_bypass_identity_in_database()

    # Not inside a try either: a server that cannot sign cannot sign
    # anybody in. Generates the deployment's first key on first start.
    await SigningKeys.load()
    _key_reload = asyncio.create_task(SigningKeys.reload_forever(), name='signing-key-reload')

    # A warning, not a gate: how many replicas share the server is not
    # something one process knows. It states both numbers in the log
    # where a deploy can see them. F116.
    await check_pool_fits_the_server()

    # Retention (DP-6): a setting is not a limit, so something has to
    # apply it. Does nothing and starts nothing unless a window is
    # configured — both default to 0, meaning keep everything.
    _retention_tasks: dict[str, asyncio.Task[None]] = {}
    retention.start(_retention_tasks)

    # A deleted organisation reaches every service, through the outbox
    # its deletion wrote. Unconditional: a service down at the time of a
    # deletion is told when it is back. See docs/service-identity.md §6.1.
    _organisation_deletions = OrganisationDeletions()
    _organisation_deletions.start()

    # The producers feed carries every producer's effective VM profile,
    # clamped to the caps; the deployment's own caps change only with a
    # redeploy, which no trigger sees. Background: the feed catches up.
    _feed_started = asyncio.create_task(ProducerFeed().started_in_background(), name='producer-feed-started')

    # The paid modules' licence: once a day while one is expiring, lapsed
    # or read-only, the deployment's audit trail says so. igni server
    # only reports the licence; each module enforces it itself.
    _licence_daily_audit = LicenceDailyAudit()
    _licence_daily_audit.start()

    # Clock skew (B8). Three things compare a timestamp this process made
    # against one the database made — session revocation, the audit
    # trail's ordering, and retention windows — and all three fail
    # quietly when the two disagree. Unconditional, because a deployment
    # with nothing else switched on still has clocks.
    _clock_skew_tasks: dict[str, asyncio.Task[None]] = {}
    await clock_skew.sample_once()  # once at startup, so a bad clock is in the boot log
    clock_skew.start(_clock_skew_tasks)

    # AI key pools (chat + codeindex)
    try:
        manager = PoolManager()
        await manager.get_chat_pool().log_pool_summary()
        await manager.get_codeindex_pool().log_pool_summary()
    except Exception as e:
        logger.warning(f'AI key pool init failed: {e}')

    yield

    # after app - cleanup
    try:
        await PoolManager().close_all_pools()
    except Exception:  # noqa: BLE001 — shutdown is not a place to raise
        # Logged rather than passed over. These pools hold upstream
        # connections, and a close that failed is the only warning
        # anybody gets that a rolling restart is leaving them behind.
        logger.exception('Could not close the AI key pools')
    await retention.stop(_retention_tasks)
    await _organisation_deletions.stop()
    await _licence_daily_audit.stop()
    _key_reload.cancel()
    _feed_started.cancel()
    await clock_skew.stop(_clock_skew_tasks)
    # The traces still held go to the collector before the process ends.
    ServerTelemetry.flush()

    logger.info('Application shutdown complete')


_show_docs = settings.app_env == 'dev'

app = FastAPI(
    title=settings.app_name,
    root_path=settings.app_root_path,
    version=settings.app_version,
    lifespan=lifespan,
    docs_url='/docs' if _show_docs else None,
    redoc_url='/redoc' if _show_docs else None,
    openapi_url='/openapi.json' if _show_docs else None,
)


# ``POST /token`` used to sit here: an OAuth2 password-form login mounted
# at the app root, outside ``/v1``, advertising "log in and obtain an
# access token". It called ``UserDB.verify_password``, which does not
# exist on ``UserDB`` — so every request to it was an AttributeError and a
# 500, for as long as it had existed. Nothing referenced it: not the
# portal, not the CLI, not a test, which is why nobody noticed.
#
# Removed rather than repaired. ``POST /v1/sign-in`` is the real password
# login and works (see ``AuthService.sign_in``), so fixing this one would
# have produced a second, unversioned way to do the same thing.


@app.get('/health', tags=['Health'], response_model=LivenessReport)
async def health_check() -> LivenessReport:
    """Liveness: this process is running and serving.

    Deliberately checks nothing. A liveness probe that fails when the
    database is unreachable gets the container killed and restarted for a
    problem a restart cannot fix, turning a database blip into a restart
    loop. Use ``/ready`` to decide whether to send traffic.
    """
    return LivenessReport(status='ok')


@app.get('/ready', tags=['Health'], response_model=ReadinessReport)
async def readiness_check(response: Response) -> ReadinessReport:
    """Readiness: this process can actually serve a request.

    ``/health`` answered ``ok`` while the API could serve nothing —
    verified by running it with Postgres and Redis unreachable: it booted,
    ``/health`` returned 200, and every database-backed route returned
    500. A load balancer reading ``/health`` would have kept sending
    traffic to it indefinitely.

    503 when a dependency is down, so an orchestrator can drain the
    instance without killing it. The checks are deliberately the cheapest
    possible round trip to each — ``SELECT 1`` and a ``PING`` — because
    this endpoint gets polled.

    **A busy pool is not an unready process.** This check used to run
    against its own private connection pool, so it answered ``ready`` in
    40ms while every repository around it was failing on pool exhaustion —
    a probe insulated from the failure it exists to detect. It shares the
    pool now, which introduces the opposite hazard: under a burst the
    check would queue, time out, report the database unavailable and get
    the pod pulled out of service — sending its traffic to the sibling
    replica, which then saturates too. A burst that should have queued
    takes the whole deployment down instead.

    So saturation and unavailability are answered differently. The check
    waits a short, fixed time; if it does not get a connection and the
    pool is full, that is ``busy`` and the process stays **ready**,
    because removing a working replica from a load balancer is the one
    action guaranteed to make a capacity problem worse. Anything else —
    the server refusing, the host unreachable, a timeout with the pool
    *not* full — is ``unavailable``, and that is a 503. F116.
    """
    report = await ReadinessProbe().run()
    if not report.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report


@app.get('/version', tags=['Health'], response_model=VersionReport)
async def version() -> VersionReport:
    return VersionReport(version=settings.app_version)


# Include API routers
app.include_router(api_v1_router, prefix='/v1')
app.include_router(identity_discovery.router)

# SCIM 2.0, at the path the standard fixes. Not under `/v1`: a
# provider is configured with a base URL and appends `/Users` itself,
# so the shape is not ours to version. Answers 503 throughout unless
# SCIM_TOKEN is set, so a deployment that has not configured
# provisioning is not running an unauthenticated one.
app.include_router(scim_users.router, prefix='/scim/v2', tags=['SCIM'])

# `GET /metrics`, 404 unless METRICS_TOKEN is set. Not under /v1: it is
# not part of the product's API and is not versioned with it.
app.include_router(metrics_router)

# Always 1; the label is the payload. Set at import so a replica reports
# its version even before it has served anything — which is the point,
# since the alert this feeds is about two replicas disagreeing during a
# half-finished rollout.
BUILD_INFO.labels(version=settings.app_version).set(1)

# Innermost of the response-side middlewares on purpose: it needs the
# resolved route, its path parameters and the actor that the auth
# dependency stashed on ``request.state``, none of which exist further out.
# ``add_middleware`` wraps outward, so the first registered runs closest to
# the endpoint — the ordering mistake F56 made in the other direction.
app.add_middleware(AuditMiddleware)

# Further out than the audit middleware, deliberately: the audit trail
# records what a user did on the routes F90's policy covers, while a 500
# is worth counting wherever it happens.
app.add_middleware(MetricsMiddleware)

app.add_middleware(TimingMiddleware)
app.add_middleware(DisableCacheMiddleware)
cors_allowlist: list[str] = []


app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allowlist,
    allow_credentials=False,
    allow_methods=['*'],
    allow_headers=['*'],
    expose_headers=['*'],
)

# Registered LAST deliberately: Starlette applies middleware in reverse
# registration order, so the final call is the outermost wrapper. That is
# what gets an oversized body refused before the timing, cache and CORS
# layers touch it — and before FastAPI parses it into Python objects,
# which is where the memory went.
#
# The first version of this registered it before TimingMiddleware, which
# made it the *innermost* of the four and would have defeated the point.
app.add_middleware(
    BodySizeLimitMiddleware,
    max_bytes=settings.max_request_body_bytes,
    larger={EXTENSION_UPLOAD_PATH: MAX_ARCHIVE_BYTES, ASSET_UPLOAD_PATH: MAX_ASSET_BYTES},
)

# OpenTelemetry traces, to the operator's own collector — only when
# OTEL_EXPORTER_OTLP_ENDPOINT names one; there is no default, and with none
# nothing is built. After the middleware above, so the request span is the
# outermost thing a request passes through. docs/observability.md §5.
ServerTelemetry.start(app, settings.otel)


if __name__ == '__main__':
    # Binding all interfaces is required inside a container; what is
    # exposed is decided by the compose port mapping, not by this line.
    # This block is the local-dev entrypoint — the image runs uvicorn from
    # its CMD and never reaches here.
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', '7777')))  # nosec B104
