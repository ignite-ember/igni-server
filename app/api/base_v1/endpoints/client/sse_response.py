"""An SSE response that closes its generator however the response ends."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from app.services.ai.chat.tails import TailRunner

#: No caching, and no proxy buffering: each frame must leave as it is made.
SSE_HEADERS = {
    'Cache-Control': 'no-cache',
    'Connection': 'keep-alive',
    'X-Accel-Buffering': 'no',
}


class StreamThatEnds(StreamingResponse):
    """A `text/event-stream` response whose generator is closed on every exit.

    Starlette cancels the response when the client disconnects; a
    generator waiting at a `yield` then would otherwise keep its
    `finally`, and the node's slot, until the garbage collector.
    """

    def __init__(self, content: AsyncGenerator[str, None], *, tails: TailRunner) -> None:
        super().__init__(content, media_type='text/event-stream', headers=SSE_HEADERS)
        self._generator = content
        self._tails = tails

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._tails.run(self._generator.aclose())
