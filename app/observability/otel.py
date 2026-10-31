"""OpenTelemetry traces, to the customer's own collector and nowhere else.

Off unless ``OTEL_EXPORTER_OTLP_ENDPOINT`` names a collector
(:class:`~app.config.OtelConfig`); there is no default endpoint, and with
none :meth:`ServerTelemetry.start` builds nothing — no tracer provider, no
instrumentation, no exporter. With one, three instrumentations feed it:

* **FastAPI** — a span per request. An incoming ``traceparent`` is
  accepted, so a turn the client traced continues here.
* **httpx** — a span per outgoing request, carrying ``traceparent`` on to
  the inference nodes, the modules and anything else igni server calls, so
  one trace follows a request across the deployment.
* **SQLAlchemy** — a span per statement, on every engine the database
  layer reports through :meth:`ServerTelemetry.watch_engine`. The
  statement is the parameterised SQL; bound values are not recorded.

**Not exported:** request and response bodies (no instrumentation here
records them), request headers, the query string and full URL (an OAuth
callback's ``code`` is in one), and the caller's address and user agent.
:class:`SpanScrubber` drops those attributes on the way out.

The providers are not installed as OpenTelemetry's process-wide defaults:
they are handed to the instrumentations, so stopping leaves the process as
it was. Traces only — the metrics a deployment scrapes are ``/metrics``
(docs/observability.md §2).
"""

from __future__ import annotations

from collections.abc import Sequence
import logging
from typing import ClassVar
from urllib.parse import urlsplit

from fastapi import FastAPI
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter as GrpcSpanExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as HttpSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy.engine import EngineTracer
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from pydantic import BaseModel
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import OtelConfig

logger = logging.getLogger(__name__)


class SpanScrubber:
    """The span attributes igni server never exports."""

    DROP: ClassVar[frozenset[str]] = frozenset(
        {
            # The full URL and query string: a callback's ``code``, a link's token.
            'http.url',
            'url.full',
            'url.query',
            'http.target',
            # Who called: their address and their browser.
            'client.address',
            'client.port',
            'http.client_ip',
            'net.peer.ip',
            'net.sock.peer.addr',
            'network.peer.address',
            'network.peer.port',
            'user_agent.original',
            'http.user_agent',
        }
    )
    #: Header capture is off unless an ``OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_*``
    #: variable turns it on; whatever such a variable asks for is dropped here.
    DROP_PREFIXES: ClassVar[tuple[str, ...]] = ('http.request.header.', 'http.response.header.')

    @classmethod
    def keeps(cls, key: str) -> bool:
        return key not in cls.DROP and not key.startswith(cls.DROP_PREFIXES)

    @classmethod
    def scrub(cls, span: ReadableSpan) -> ReadableSpan:
        return ReadableSpan(
            name=span.name,
            context=span.context,
            parent=span.parent,
            resource=span.resource,
            attributes={k: v for k, v in (span.attributes or {}).items() if cls.keeps(k)},
            events=span.events,
            links=span.links,
            kind=span.kind,
            status=span.status,
            start_time=span.start_time,
            end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        )


class ScrubbingExporter(SpanExporter):
    """Hands the wrapped exporter :class:`SpanScrubber`'s copies."""

    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._inner.export([SpanScrubber.scrub(span) for span in spans])

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._inner.force_flush(timeout_millis)


class TelemetryStart(BaseModel):
    """What :meth:`ServerTelemetry.start` did: started, or why not."""

    ok: bool
    #: Empty for the ordinary case of no endpoint, which needs no warning.
    reason: str = ''


class ServerTelemetry:
    """The process's one tracer provider, or none.

    Held on the class: the instrumentations it feeds are process-wide (httpx's
    transports and the engines' event listeners are patched once for the whole
    process), and the database layer reports engines from wherever it makes
    them.
    """

    _provider: ClassVar[TracerProvider | None] = None
    _app: ClassVar[FastAPI | None] = None
    #: Paths not traced: scrapes and probes, many times a minute.
    EXCLUDED_URLS: ClassVar[str] = '/metrics$,/health$,/ready$,/version$'

    @classmethod
    def start(cls, app: FastAPI, config: OtelConfig, *, span_exporter: SpanExporter | None = None) -> TelemetryStart:
        """Trace ``app`` to ``config``'s collector, if one is named.

        ``span_exporter`` replaces the OTLP exporter, for tests.
        """
        if not config.enabled:
            return TelemetryStart(ok=False)
        parts = urlsplit(config.endpoint)
        if parts.scheme.lower() not in ('http', 'https') or not parts.hostname:
            reason = (
                f'OTEL_EXPORTER_OTLP_ENDPOINT {config.endpoint!r} is not an http:// or https:// URL; '
                'no traces are exported.'
            )
            logger.warning(reason)
            return TelemetryStart(ok=False, reason=reason)
        cls.stop()
        provider = TracerProvider(resource=Resource.create({'service.name': config.service_name}))
        provider.add_span_processor(BatchSpanProcessor(ScrubbingExporter(span_exporter or cls._otlp(config))))
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=provider,
            excluded_urls=cls.EXCLUDED_URLS,
            exclude_spans=['receive', 'send'],
        )
        HTTPXClientInstrumentor().instrument(tracer_provider=provider)
        cls._provider, cls._app = provider, app
        logger.info('Exporting OpenTelemetry traces to %s (%s).', config.endpoint, config.exporter_otlp_protocol)
        return TelemetryStart(ok=True)

    @staticmethod
    def _otlp(config: OtelConfig) -> SpanExporter:
        base = config.endpoint.rstrip('/')
        if config.exporter_otlp_protocol == 'grpc':
            return GrpcSpanExporter(endpoint=base, headers=config.headers(), insecure=base.startswith('http://'))
        return HttpSpanExporter(endpoint=f'{base}/v1/traces', headers=config.headers())

    @classmethod
    def watch_engine(cls, engine: AsyncEngine | Engine) -> None:
        """Trace ``engine``'s statements. The database layer calls this for every engine it makes."""
        if cls._provider is None:
            return
        sync_engine = engine.sync_engine if isinstance(engine, AsyncEngine) else engine
        meter = NoOpMeterProvider().get_meter(__name__)
        EngineTracer(
            cls._provider.get_tracer('app.db'),
            sync_engine,
            meter.create_up_down_counter('db.client.connections.usage'),
        )

    @classmethod
    def flush(cls) -> None:
        if cls._provider is not None:
            cls._provider.force_flush()

    @classmethod
    def stop(cls) -> None:
        """Export what is held and stop instrumenting. Safe when not started."""
        provider, app = cls._provider, cls._app
        cls._provider, cls._app = None, None
        if provider is None:
            return
        if app is not None:
            FastAPIInstrumentor.uninstrument_app(app)
        HTTPXClientInstrumentor().uninstrument()
        EngineTracer.remove_all_event_listeners()
        provider.shutdown()
