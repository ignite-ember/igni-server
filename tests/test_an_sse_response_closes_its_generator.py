"""`StreamThatEnds` closes the stream's generator however the response ends.

The generator's `finally` writes the usage row and hands back the node's
slot. Starlette cancels a response whose client left, and does not close
a generator suspended at a `yield`; without this the slot waited for the
garbage collector. Driven over raw ASGI, as a server would.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest

from app.api.base_v1.endpoints.client.sse_response import StreamThatEnds
from app.services.ai.chat.tails import TailRunner

_SCOPE = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'}, 'method': 'POST', 'headers': []}


class _Stream:
    """A generator that yields one frame, then waits for a next that never comes."""

    def __init__(self) -> None:
        self.closed = False
        self.sent_first = asyncio.Event()

    async def frames(self) -> AsyncGenerator[str, None]:
        try:
            yield 'data: one\n\n'
            await asyncio.Event().wait()
            yield 'data: never\n\n'
        finally:
            await asyncio.sleep(0)  # a tail that awaits, as the usage write does
            self.closed = True


async def _never_disconnects() -> dict:
    await asyncio.Event().wait()
    return {'type': 'http.disconnect'}


def test_it_is_an_event_stream_that_no_proxy_buffers_or_caches():
    async def nothing() -> AsyncGenerator[str, None]:
        yield ''

    response = StreamThatEnds(nothing(), tails=TailRunner())

    assert response.media_type == 'text/event-stream'
    assert response.headers['content-type'].startswith('text/event-stream')
    assert response.headers['cache-control'] == 'no-cache'
    assert response.headers['connection'] == 'keep-alive'
    assert response.headers['x-accel-buffering'] == 'no'


async def test_a_response_cancelled_mid_stream_closes_the_generator():
    """Cancelled while the frame is being sent: the generator is suspended
    at its `yield`, where a cancellation never reaches it."""
    stream = _Stream()

    async def send(message: dict) -> None:
        if message.get('body'):
            stream.sent_first.set()
            await asyncio.Event().wait()  # a slow client's socket

    task = asyncio.ensure_future(StreamThatEnds(stream.frames(), tails=TailRunner())(_SCOPE, _never_disconnects, send))
    async with asyncio.timeout(2):
        await stream.sent_first.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert stream.closed, "the generator's finally — the usage row and the slot — did not run"


async def test_a_send_that_fails_closes_the_generator():
    stream = _Stream()

    async def send(message: dict) -> None:
        if message.get('body'):
            raise OSError('connection reset')

    with pytest.raises(Exception):  # noqa: B017 — Starlette's own wrapping of the OSError
        await StreamThatEnds(stream.frames(), tails=TailRunner())(_SCOPE, _never_disconnects, send)

    assert stream.closed
