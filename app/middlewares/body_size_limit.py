"""Refuse an oversized request body before it becomes memory.

Nothing bounded request size — not the app, not the container, not the
Dockerfile. Measured against a running API: a 193 MB body was read and
parsed in full, grew RSS by ~135 MB, and *then* answered 404 because the
model name was unknown. The rejection had nothing to do with the size; the
body had already been paid for.

With rate limiting deliberately absent from this product, a handful of
concurrent requests from one authenticated user is all that needs.

Two checks, because one is not enough:

* ``Content-Length``, when present, is refused up front — no bytes read.
* the stream is counted as it arrives, which is what catches a chunked
  request (no ``Content-Length`` at all).

**Pure ASGI rather than ``BaseHTTPMiddleware``, and that is load-bearing.**
The first version subclassed ``BaseHTTPMiddleware`` and raised from the
wrapped ``receive``. Memory stayed bounded — RSS stopped dead at the cap —
but the *status* was wrong: ``receive`` is called from inside the
downstream app, so the exception surfaced there and FastAPI answered
``400 {"detail":"There was an error parsing the body"}``. An operator
debugging that would look at the payload's syntax, not its size. At this
level the 413 can be sent directly.

A note on where this does *not* apply: the portal's nginx proxies ``/v1/``
and has its own ``client_max_body_size``. The API is directly reachable
too, and that is the path the CLI uses — so the limit has to live here as
well as there.
"""

from __future__ import annotations

import json
import logging
import re

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


class BodySizeLimitMiddleware:
    """Answer 413 rather than reading an unbounded body.

    One limit for everything, except the routes in `larger`, each with a
    limit of its own — a service's extension bundle carries a wheelhouse
    for every platform, and is tens of megabytes by design.
    """

    def __init__(self, app: ASGIApp, max_bytes: int, larger: dict[str, int] | None = None) -> None:
        if max_bytes <= 0:
            raise ValueError('max_bytes must be positive')
        self.app = app
        self._default = max_bytes
        self._larger = [(re.compile(pattern), limit) for pattern, limit in (larger or {}).items()]

    def _limit_for(self, path: str) -> int:
        return next((limit for pattern, limit in self._larger if pattern.search(path)), self._default)

    async def _send_413(self, send: Send, seen: int | None, limit: int) -> None:
        # The limit is named so a caller can act on it; the observed size
        # appears only when it is actually known.
        detail = f'Request body exceeds the {limit} byte limit.'
        if seen is not None:
            detail = f'Request body is {seen} bytes, over the {limit} byte limit.'
        payload = json.dumps({'detail': detail}).encode()
        await send(
            {
                'type': 'http.response.start',
                'status': 413,
                'headers': [
                    (b'content-type', b'application/json'),
                    (b'content-length', str(len(payload)).encode()),
                    # The client is mid-upload; there is no point reading the
                    # rest of a body that has already been refused.
                    (b'connection', b'close'),
                ],
            }
        )
        await send({'type': 'http.response.body', 'body': payload})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        limit = self._limit_for(str(scope.get('path') or ''))
        declared = Headers(scope=scope).get('content-length')
        if declared is not None:
            try:
                announced = int(declared)
            except ValueError:
                pass  # malformed header; let normal parsing answer it
            else:
                if announced > limit:
                    # Nothing has been read yet, which is the whole point.
                    logger.warning(
                        'Refused a %d byte body (limit %d) on %s before reading it',
                        announced,
                        limit,
                        scope.get('path'),
                    )
                    await self._send_413(send, announced, limit)
                    return

        # No Content-Length, or one that claims to fit. Count what actually
        # arrives so a chunked upload cannot walk past the cap.
        state = {'received': 0, 'refused': False, 'response_started': False}

        async def counting_receive() -> Message:
            message = await receive()
            if message['type'] == 'http.request':
                state['received'] += len(message.get('body') or b'')
                if state['received'] > limit and not state['refused']:
                    state['refused'] = True
                    logger.warning(
                        'Aborted a body that reached %d bytes (limit %d) on %s',
                        state['received'],
                        limit,
                        scope.get('path'),
                    )
                    # Present it downstream as a disconnect rather than a
                    # truncated body: a half-read JSON document would be
                    # reported as a syntax error, which is the wrong story.
                    return {'type': 'http.disconnect'}
            return message

        async def guarded_send(message: Message) -> None:
            # Order matters, and getting it wrong cost a debugging round.
            # Setting the flag before this check recorded "the app tried to
            # respond" rather than "bytes reached the client" — so a refused
            # request looked like one that had already answered, the 413 was
            # skipped, and uvicorn logged "ASGI callable returned without
            # starting response" and sent a bare 500.
            if state['refused']:
                return
            if message['type'] == 'http.response.start':
                state['response_started'] = True
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except Exception:
            # A refused body is reported downstream as a disconnect, and
            # Starlette turns that into a ClientDisconnect — which
            # propagated all the way out as a bare 500 before this catch
            # existed. When the refusal is ours, the exception is the
            # expected consequence of it and the 413 below is the answer.
            if not state['refused']:
                raise

        if state['refused'] and not state['response_started']:
            await self._send_413(send, state['received'], limit)
        elif state['refused']:
            # The app had already begun responding before the overflow was
            # seen, so a 413 cannot be sent any more. Recorded because it
            # means a large body was read past the cap on this request.
            logger.error(
                'Body exceeded the limit on %s after the response had started; could not answer 413',
                scope.get('path'),
            )
