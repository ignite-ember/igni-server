"""igni server's traces go to the operator's collector, and only there.

`app/observability/otel.py` traces requests, outgoing httpx calls and SQL
statements. These tests hold what `tests/test_nothing_phones_home.py`
allows the OTLP exporters for:

- with no `OTEL_EXPORTER_OTLP_ENDPOINT` nothing is set up at all;
- the endpoint comes from the process environment, and from nothing else;
- a request is traced without its body, its query string or its caller's
  address.

And that a trace is one trace across the deployment: a `traceparent` the
client sent is the request's parent, and the same trace id goes out on the
httpx call igni server makes on to an inference node or a module.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import httpx
from opentelemetry import trace as otel_trace
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
import pytest
from sqlalchemy import create_engine, text

from app.config import OtelConfig, Settings
from app.observability.otel import ServerTelemetry

TRACE_ID = '4bf92f3577b34da6a3ce929d0e0e4736'
TRACEPARENT = f'00-{TRACE_ID}-00f067aa0ba902b7-01'
BODY_SECRET = 'customer-source-in-a-body-91c2'
QUERY_SECRET = 'oauth-code-in-a-query-55d0'

_OTEL_VARIABLES = (
    'OTEL_EXPORTER_OTLP_ENDPOINT',
    'OTEL_EXPORTER_OTLP_PROTOCOL',
    'OTEL_EXPORTER_OTLP_HEADERS',
    'OTEL_SERVICE_NAME',
)


class Upstream:
    """Stands in for an inference node: records the headers it was sent."""

    def __init__(self) -> None:
        self.headers: list[dict[str, str]] = []
        self._server = ThreadingHTTPServer(('127.0.0.1', 0), _UpstreamHandler)
        self._server.upstream = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f'http://{host}:{port}/v1/chat/completions'

    def __enter__(self) -> Upstream:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class _UpstreamHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 — the stdlib's name
        self.rfile.read(int(self.headers.get('Content-Length') or 0))
        self.server.upstream.headers.append({k.lower(): v for k, v in self.headers.items()})  # type: ignore[attr-defined]
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


def relay_app(upstream_url: str) -> FastAPI:
    """One route that does what the chat proxy does: takes a body, calls upstream."""
    app = FastAPI()

    @app.post('/v1/relay')
    async def relay(request: Request) -> dict[str, bool]:
        await request.json()
        async with httpx.AsyncClient() as client:
            await client.post(upstream_url, json={'messages': []})
        return {'ok': True}

    return app


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    for name in _OTEL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    yield
    ServerTelemetry.stop()


def configured(endpoint: str = 'http://otel-collector:4318') -> OtelConfig:
    return OtelConfig(exporter_otlp_endpoint=endpoint)


class TestNoCollectorNoTracing:
    def test_with_no_endpoint_nothing_is_set_up(self):
        config = OtelConfig()
        app = FastAPI()

        started = ServerTelemetry.start(app, config)

        assert config.enabled is False
        assert started.ok is False and started.reason == ''
        assert ServerTelemetry._provider is None
        assert not HTTPXClientInstrumentor().is_instrumented_by_opentelemetry
        assert not getattr(app, '_is_instrumented_by_opentelemetry', False)
        assert not isinstance(otel_trace.get_tracer_provider(), TracerProvider)

    def test_an_endpoint_that_is_not_a_url_is_refused(self, caplog):
        started = ServerTelemetry.start(FastAPI(), configured('otel-collector:4318'))

        assert started.ok is False
        assert 'not an http:// or https:// URL' in caplog.text


class TestTheEndpointIsTheOperators:
    def test_the_endpoint_comes_from_the_environment_only(self, monkeypatch, tmp_path):
        """The standard variables; and a `.env` file, which the rest of the
        settings read, does not set it."""
        (tmp_path / '.env').write_text('OTEL_EXPORTER_OTLP_ENDPOINT=http://from-a-dotenv:4318\n')
        monkeypatch.chdir(tmp_path)

        assert Settings().otel.endpoint == ''

        monkeypatch.setenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://otel.acme.internal:4318')
        monkeypatch.setenv('OTEL_EXPORTER_OTLP_PROTOCOL', 'grpc')
        monkeypatch.setenv('OTEL_EXPORTER_OTLP_HEADERS', 'Authorization=Bearer%20t')
        monkeypatch.setenv('OTEL_SERVICE_NAME', 'igni-api')
        otel = Settings().otel

        assert otel.endpoint == 'http://otel.acme.internal:4318'
        assert otel.exporter_otlp_protocol == 'grpc'
        assert otel.headers() == {'authorization': 'Bearer t'}
        assert otel.service_name == 'igni-api'

    def test_there_is_no_default_endpoint(self):
        assert OtelConfig.model_fields['exporter_otlp_endpoint'].default == ''


class TestWhatIsTraced:
    def test_a_request_is_traced_without_its_body(self):
        exported = InMemorySpanExporter()
        with Upstream() as upstream:
            app = relay_app(upstream.url)
            assert ServerTelemetry.start(app, configured(), span_exporter=exported).ok
            response = TestClient(app).post(
                f'/v1/relay?code={QUERY_SECRET}',
                json={'source': BODY_SECRET},
                headers={'traceparent': TRACEPARENT, 'user-agent': 'igni-cli/1.0'},
            )
            ServerTelemetry.flush()

        assert response.status_code == 200
        spans = exported.get_finished_spans()
        server = [s for s in spans if s.kind is otel_trace.SpanKind.SERVER]
        client = [s for s in spans if s.kind is otel_trace.SpanKind.CLIENT]
        assert server and client

        # The client's trace continues here, and goes on to the upstream.
        assert {format(s.context.trace_id, '032x') for s in spans} == {TRACE_ID}
        (sent,) = upstream.headers
        assert sent['traceparent'].split('-')[1] == TRACE_ID
        assert server[0].attributes['http.route'] == '/v1/relay'

        # And none of what the request carried.
        everything = json.dumps([dict(s.attributes or {}) for s in spans] + [s.name for s in spans])
        for leaked in (BODY_SECRET, QUERY_SECRET, 'igni-cli/1.0', '127.0.0.1:'):
            assert leaked not in everything, f'{leaked!r} was exported'

    def test_a_statement_is_traced_on_an_engine_the_database_layer_reports(self):
        exported = InMemorySpanExporter()
        assert ServerTelemetry.start(FastAPI(), configured(), span_exporter=exported).ok
        engine = create_engine('sqlite://')
        ServerTelemetry.watch_engine(engine)

        with engine.connect() as connection:
            connection.execute(text('SELECT 1 WHERE 1 = :one'), {'one': 1})
        ServerTelemetry.flush()

        (statement,) = [s for s in exported.get_finished_spans() if s.attributes.get('db.system') == 'sqlite']
        assert 'SELECT 1 WHERE 1 = ?' in str(statement.attributes.get('db.statement'))

    def test_an_engine_reported_while_off_is_left_alone(self):
        engine = create_engine('sqlite://')
        ServerTelemetry.watch_engine(engine)

        with engine.connect() as connection:
            assert connection.execute(text('SELECT 1')).scalar() == 1
