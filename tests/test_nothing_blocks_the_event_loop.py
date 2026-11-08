"""One request must not be able to stop the process.

F118. Three OAuth sign-in paths called **synchronous** `httpx.post` and
`httpx.get` from inside `async def`. A synchronous HTTP call does not
yield the event loop, it occupies the thread — and the API runs a single
uvicorn worker (F116), so for the duration of one sign-in the whole
process served nothing: no chat, no portal, no `/health`, no `/ready`.

Measured against a deliberately slow token endpoint before the fix:

    /health, nothing else happening   : 0.00s
    /health, during one OAuth sign-in : 3.10s   <- upstream sleeps 3.0s

With the timeouts that were configured — 15s for the token exchange plus
10s for the profile plus 10s for the emails — the worst case was 35
seconds of a completely dead API per sign-in, and concurrent sign-ins
serialise, so three unlucky ones during a provider incident is nearly two
minutes. The readiness probe times out at 5 seconds, so the pod also
leaves the load balancer, sending its traffic to a sibling doing exactly
the same thing.

## The rule is absolute, and that is the point

A lexical rule — "no blocking call inside an `async def`" — found five of
the seven and **missed two**: `_google_exchange_and_get_user` was a plain
`def` called from an async function, which blocks identically. The same
shape as F111's directories and F115's column names: the rule was looking
in the right place for the wrong thing.

So the rule here is not about context at all. There is no legitimate
synchronous HTTP call anywhere in `app/`: since DEC-13 removed the
worker, every entry point in the application is a FastAPI coroutine on
uvicorn's loop, and before that the alternative was a Celery task on a
shared loop — which had the same problem for the same reason. Zero,
everywhere, with no exemption list to argue about.
"""

from __future__ import annotations

import ast
import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import pathlib
import threading
import time

import httpx
import pytest

pytestmark = pytest.mark.anyio

_APP = pathlib.Path(__file__).resolve().parent.parent / 'app'

#: Callables that occupy the thread rather than yielding it. The
#: module-level functions of each library, which are the ones that build
#: a client, make the request and block — the async client's methods are
#: attributes of an instance and never match this shape.
_BLOCKING_HTTP = {
    'httpx': {'get', 'post', 'put', 'patch', 'delete', 'request', 'head', 'stream'},
    'requests': {'get', 'post', 'put', 'patch', 'delete', 'request', 'head', 'Session'},
}


def _attribute_chain(node: ast.expr) -> tuple[str, ...]:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return tuple(reversed(parts))


def _synchronous_http_calls() -> list[str]:
    """Every module-level `httpx.get`-shaped call under `app/`."""
    found: list[str] = []
    for path in sorted(_APP.rglob('*.py')):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            chain = _attribute_chain(node.func)
            if len(chain) == 2 and chain[1] in _BLOCKING_HTTP.get(chain[0], set()):
                found.append(f'{path.relative_to(_APP.parent)}:{node.lineno} {".".join(chain)}()')
    return found


class TestTheRule:
    def test_no_synchronous_http_call_exists_in_the_application(self):
        offenders = _synchronous_http_calls()

        assert not offenders, (
            'these calls block the thread instead of yielding the event loop:\n  '
            + '\n  '.join(offenders)
            + '\n\nThe API runs one uvicorn worker, so a blocking call stops every request '
            'in that process for as long as the far end takes — including the retention '
            'sweep and the clock-skew probe, which share the loop. Use '
            '`httpx.AsyncClient` and `await`. If a synchronous call is genuinely '
            'unavoidable, it has to go through `asyncio.to_thread`.'
        )

    def test_the_scan_reads_the_application(self):
        """A rule that measured nothing would pass silently.

        `_synchronous_http_calls` returning an empty list is the whole
        assertion above, so "found no files" and "found no offenders"
        have to be distinguishable.
        """
        modules = list(_APP.rglob('*.py'))
        assert len(modules) > 150, f'only {len(modules)} modules found under {_APP}'

        # And it can still recognise the thing it is looking for.
        tree = ast.parse('import httpx\nhttpx.post("http://x")\n')
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        assert _attribute_chain(calls[0].func) == ('httpx', 'post')

    def test_the_async_client_is_not_mistaken_for_a_blocking_one(self):
        """`client.post(...)` on an AsyncClient instance must not match.

        The fix replaced every offender with exactly that shape, so a
        rule that flagged it would have made the fix unmergeable and the
        obvious workaround would have been to weaken the rule.
        """
        source = (
            'async def f():\n    async with httpx.AsyncClient() as client:\n        await client.post("http://x")\n'
        )
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call):
                chain = _attribute_chain(node.func)
                if len(chain) == 2:
                    assert chain[1] not in _BLOCKING_HTTP.get(chain[0], set()), chain


class _SlowProvider(BaseHTTPRequestHandler):
    """An OAuth provider that takes its time answering."""

    delay = 2.0

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's contract
        time.sleep(self.delay)
        self._json({'access_token': 'a-token'})

    def do_GET(self) -> None:  # noqa: N802
        self._json({'login': 'someone', 'email': 'someone@example.com', 'name': 'Some One'})

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


class TestTheBehaviour:
    """The rule above is static. This is the thing it protects."""

    async def test_a_slow_sign_in_does_not_stall_everything_else(self):
        """Both requests from one starting gun.

        The first version of this measurement started its clock *after*
        awaiting the sign-in for a moment — so the block had already
        finished by the time `/health` was timed, and it reported 0.00s
        and concluded the loop was fine. A stall can only be seen from a
        common start.
        """
        from app.main import app
        from app.services.auth.google_sign_in import GoogleSignIn
        from app.services.auth.oauth_models import OAuthProvider
        from app.services.auth.oauth_service import OAuthService

        server = HTTPServer(('127.0.0.1', 0), _SlowProvider)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        oauth = OAuthService(
            google=GoogleSignIn(
                token_url=f'http://127.0.0.1:{port}/token',
                userinfo_url=f'http://127.0.0.1:{port}/user',
            )
        )

        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
                start = time.monotonic()
                answered: dict[str, float] = {}

                async def ask_health() -> None:
                    # Let the sign-in reach its first outbound call.
                    await asyncio.sleep(0.05)
                    response = await client.get('/health')
                    assert response.status_code == 200
                    answered['at'] = time.monotonic() - start

                await asyncio.gather(
                    oauth.exchange_and_get_user(OAuthProvider.GOOGLE, 'a-code'),
                    ask_health(),
                )

            # The sign-in waits `_SlowProvider.delay`; a health check
            # behind a blocked loop waits the same. Half of it is a wide
            # margin that still cannot be reached by accident.
            assert answered['at'] < _SlowProvider.delay / 2, (
                f'/health took {answered["at"]:.2f}s while one sign-in was in flight, and the '
                f'upstream only sleeps {_SlowProvider.delay}s — the event loop was blocked, so '
                "every request in this process was waiting on somebody else's OAuth provider."
            )
        finally:
            server.shutdown()
            thread.join(timeout=5)
