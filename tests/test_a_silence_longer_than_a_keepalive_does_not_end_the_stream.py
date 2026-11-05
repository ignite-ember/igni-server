"""An upstream that goes quiet for longer than the keepalive interval
is still being read when it speaks again.

`lines_with_keepalive` waited for each line with
`asyncio.wait_for(lines.__anext__(), timeout=interval)`. When the time
ran out, `wait_for` cancelled the pending `__anext__` — and cancelling
`__anext__` on an async generator closes the generator. The keepalive
went out, the next read raised `StopAsyncIteration`, and the stream
ended as though the model had finished: no error, usage estimated, an
empty turn. Fed `first`, a gap, then `second`, it produced

    ['data: first', ': keepalive']

In practice: any upstream silence longer than the interval — a long
vLLM prefill, or mlx while it generates a tool call.

These run with intervals in tens of milliseconds; the behaviour does
not depend on the scale.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import time

import httpx
import pytest

from app.services.ai.sse_keepalive import KEEPALIVE_LINE, lines_with_keepalive
from app.services.ai.upstream import UpstreamStale

INTERVAL = 0.05


class _Upstream:
    """A response whose `aiter_lines` follows a script of lines and pauses.

    Records whether the line generator was closed, and whether it was
    closed by a cancellation, so a test can tell "the upstream read was
    torn down" from "the upstream was simply never read again".
    """

    def __init__(self, script: list[str | float]):
        self.script = script
        self.closed = False
        self.cancelled = False

    async def aiter_lines(self) -> AsyncIterator[str]:
        try:
            for step in self.script:
                if isinstance(step, float):
                    await asyncio.sleep(step)
                else:
                    yield step
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self.closed = True


async def _collect(response, **kwargs) -> list[str]:
    return [line async for line in lines_with_keepalive(response, **kwargs)]


async def test_the_reported_case_both_lines_arrive():
    """`first`, a gap of three intervals, `second` — both are delivered."""
    out = await _collect(
        _Upstream(['data: first', INTERVAL * 3.2, 'data: second']),
        interval=INTERVAL,
        stale_timeout=10,
    )
    data = [line for line in out if line != KEEPALIVE_LINE]
    assert data == ['data: first', 'data: second']
    assert out.count(KEEPALIVE_LINE) >= 2


async def test_a_gap_shorter_than_the_interval_sends_no_keepalive():
    out = await _collect(
        _Upstream(['data: a', INTERVAL / 5, 'data: b', INTERVAL / 5, 'data: c']),
        interval=INTERVAL,
        stale_timeout=10,
    )
    assert out == ['data: a', 'data: b', 'data: c']


async def test_a_gap_longer_than_the_interval_is_filled_and_then_read_through():
    out = await _collect(
        _Upstream([INTERVAL * 1.5, 'data: late']),
        interval=INTERVAL,
        stale_timeout=10,
    )
    assert out == [KEEPALIVE_LINE, 'data: late']


async def test_several_gaps_in_one_stream_lose_nothing():
    script: list[str | float] = []
    for i in range(4):
        script += [f'data: {i}', INTERVAL * 2.5]
    script.append('data: [DONE]')

    out = await _collect(_Upstream(script), interval=INTERVAL, stale_timeout=10)

    assert [line for line in out if line != KEEPALIVE_LINE] == [
        'data: 0',
        'data: 1',
        'data: 2',
        'data: 3',
        'data: [DONE]',
    ]
    # Each gap is two-and-a-half intervals: at least two keepalives per gap.
    assert out.count(KEEPALIVE_LINE) >= 8


async def test_a_clean_end_ends_the_stream_without_a_keepalive_after_it():
    upstream = _Upstream(['data: x', 'data: [DONE]'])
    out = await _collect(upstream, interval=INTERVAL, stale_timeout=10)
    assert out == ['data: x', 'data: [DONE]']
    assert upstream.closed and not upstream.cancelled


async def test_real_silence_past_the_stale_timeout_raises_and_tears_the_read_down():
    upstream = _Upstream(['data: x', 60.0, 'data: never'])
    seen: list[str] = []
    t0 = time.monotonic()

    with pytest.raises(UpstreamStale):
        async for line in lines_with_keepalive(upstream, interval=INTERVAL, stale_timeout=INTERVAL * 4):
            seen.append(line)

    assert time.monotonic() - t0 < 2, 'stale should be declared after ~stale_timeout, not after the gap'
    assert seen[0] == 'data: x'
    assert set(seen[1:]) == {KEEPALIVE_LINE}
    # The outstanding read is cancelled, not left running in the background.
    assert upstream.cancelled and upstream.closed


async def test_keepalives_do_not_reset_the_stale_clock_but_data_does():
    """Data every 3 intervals keeps a 4-interval stale timeout from firing,
    though the keepalives in between are not what keeps it alive."""
    script: list[str | float] = []
    for i in range(5):
        script += [INTERVAL * 3, f'data: {i}']

    out = await _collect(_Upstream(script), interval=INTERVAL, stale_timeout=INTERVAL * 4)

    assert [line for line in out if line != KEEPALIVE_LINE] == [f'data: {i}' for i in range(5)]


async def test_a_consumer_that_stops_cancels_the_upstream_read():
    """A client disconnect closes the response generator — `aclose()` is
    what Starlette does. The read pending on the upstream must go with it,
    or the node keeps generating for nobody."""
    upstream = _Upstream(['data: x', 60.0, 'data: never'])
    gen = lines_with_keepalive(upstream, interval=INTERVAL, stale_timeout=60)

    assert await gen.__anext__() == 'data: x'
    assert await gen.__anext__() == KEEPALIVE_LINE  # a read is now outstanding
    await gen.aclose()

    assert upstream.cancelled and upstream.closed


async def test_a_consumer_cancelled_mid_wait_cancels_the_upstream_read():
    upstream = _Upstream(['data: x', 60.0, 'data: never'])

    async def consume():
        async with asyncio.timeout(INTERVAL * 3):
            async for _ in lines_with_keepalive(upstream, interval=INTERVAL * 10, stale_timeout=60):
                pass

    with pytest.raises(TimeoutError):
        await consume()

    assert upstream.cancelled and upstream.closed


async def test_through_a_real_httpx_stream():
    """The same, over `httpx`'s own `aiter_lines`, which is what the chat
    proxy hands this in production."""

    class _Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: first\n\n'
            await asyncio.sleep(INTERVAL * 3.2)
            yield b'data: second\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_Body(), headers={'content-type': 'text/event-stream'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async with client.stream('POST', 'http://node.invalid/chat/completions') as resp:
            out = await _collect(resp, interval=INTERVAL, stale_timeout=10)

    assert [line for line in out if line and line != KEEPALIVE_LINE] == ['data: first', 'data: second']
    assert KEEPALIVE_LINE in out
