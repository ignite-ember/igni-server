"""The caller's connection: whether it is still there, and what to log when it is not."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
import logging
from typing import Any, TypeVar

from fastapi import Request
from fastapi.responses import Response
from uvicorn.protocols.utils import get_client_addr, get_path_with_query_string

logger = logging.getLogger(__name__)

_T = TypeVar('_T')

#: nginx's "client closed request". Never seen by the client, which has
#: gone; it is what the access log says about the request.
CLIENT_CLOSED_REQUEST = 499


class ClientConnection:
    """One request's connection, watched through `receive` rather than polled."""

    def __init__(self, request: Request, *, access_log: logging.Logger | None = None) -> None:
        self._request = request
        self._access_log = access_log or logging.getLogger('uvicorn.access')

    def closed_response(self) -> Response:
        """A 499 for a caller who left, with its access line written here.

        uvicorn writes no access line for a response to a closed
        connection, which is the only kind a 499 is for; the line has
        uvicorn's shape so whatever counts outcomes counts this one.
        """
        # Starlette's scope is a plain mapping; uvicorn's helpers are typed
        # for its own scope TypedDicts, which this is at runtime.
        scope: Any = self._request.scope
        self._access_log.info(
            '%s - "%s %s HTTP/%s" %d',
            get_client_addr(scope),
            scope['method'],
            get_path_with_query_string(scope),
            scope.get('http_version', '1.1'),
            CLIENT_CLOSED_REQUEST,
        )
        return Response(status_code=CLIENT_CLOSED_REQUEST)

    async def until_closed(self) -> None:
        """Return when the caller's connection closes.

        Waits on `receive`: `request.is_disconnected()` never sees a
        disconnect behind `BaseHTTPMiddleware`. The body has been read by
        now, so the only message left to come is the disconnect.
        """
        while True:
            message = await self._request.receive()
            if message.get('type') == 'http.disconnect':
                return

    async def unless_it_closes(self, work: Coroutine[Any, Any, _T]) -> _T | None:
        """Run *work*, cancelling it if the caller leaves first; None when they did.

        A non-streamed handler is not cancelled on a disconnect, so
        without this a caller who gave up still had its completion
        generated and billed. Cancelling the work closes the upstream
        call, and vLLM aborts a request whose connection closes.
        """
        task = asyncio.ensure_future(work)
        watcher = asyncio.ensure_future(self.until_closed())
        try:
            await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if task.done():
                return task.result()
            if watcher.exception() is not None:
                # Could not tell whether the caller is there; a receive that
                # failed is no reason to throw away their answer.
                logger.warning('Could not watch for a disconnect: %s', watcher.exception())
                return await task
            logger.info('Client left before the completion arrived; cancelling the upstream call')
            return None
        finally:
            # `wait`, not `await`: each one's own CancelledError is expected
            # and not ours to raise, while a cancellation aimed at this
            # handler still propagates.
            for pending in (task, watcher):
                if not pending.done():
                    pending.cancel()
                    await asyncio.wait({pending})
