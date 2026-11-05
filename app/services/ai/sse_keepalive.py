"""Keeping an SSE stream alive, and noticing when it has died.

Two different silences, told apart. A model thinking for ten seconds
sends nothing and is working; an upstream that has dropped the
connection also sends nothing. A proxy that cannot distinguish them
either kills healthy long generations or hangs forever on dead ones.

So: a comment frame every `interval` seconds keeps intermediaries from
timing the connection out, and `stale_timeout` of *real* silence — the
keepalives this emits do not count — says the upstream is gone.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import time
from typing import Any

from app.services.ai.upstream import UpstreamStale

#: Seconds between SSE keepalive comments.
KEEPALIVE_INTERVAL = 8

#: Seconds of no upstream data before the stream is declared dead.
STALE_TIMEOUT = 30

#: The comment frame itself. A line beginning with `:` is a comment in
#: the SSE grammar, so a client parses and discards it.
KEEPALIVE_LINE = ': keepalive'


async def lines_with_keepalive(
    response: Any,
    *,
    interval: float = KEEPALIVE_INTERVAL,
    stale_timeout: float = STALE_TIMEOUT,
) -> AsyncIterator[str]:
    """Yield upstream lines, with keepalives filling the gaps.

    Raises `UpstreamStale` once *stale_timeout* has passed with nothing
    from the upstream. The clock resets on real data only, which is the
    whole point: a long generation keeps the connection open through
    its own output, and a dead one runs the timer out.

    **One read is outstanding across keepalive ticks, and a tick never
    cancels it.** This was `asyncio.wait_for(lines.__anext__(), interval)`,
    and `wait_for` cancels what it waits on when the time runs out.
    Cancelling `__anext__` on an async generator closes the generator —
    so the first keepalive was also the last thing the stream ever said:
    the next read raised `StopAsyncIteration`, this returned as though
    the upstream had finished, and the turn ended empty with its usage
    estimated. Fed `first`, a 3-second gap, then `second` at a 1-second
    interval, it produced `['data: first', ': keepalive']`. Any upstream
    silence longer than the interval did it — a long vLLM prefill, or
    mlx while it generates a tool call.

    The pending read is cancelled only when this generator itself ends:
    the upstream went stale, or the consumer stopped (a client
    disconnect closes it), and then cancelling is the point.
    """
    lines = response.aiter_lines().__aiter__()
    last_data = time.monotonic()
    pending: asyncio.Future[str] | None = None

    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(lines.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                if time.monotonic() - last_data > stale_timeout:
                    raise UpstreamStale(f'No data from upstream for {stale_timeout}s')
                yield KEEPALIVE_LINE
                continue

            finished, pending = pending, None
            try:
                line = finished.result()
            except StopAsyncIteration:
                return

            last_data = time.monotonic()
            yield line
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            # Let the cancellation land, so the upstream read is actually
            # torn down before whoever closed this moves on. `wait`, not
            # `await pending`: it does not re-raise the read's own
            # CancelledError, and a cancellation aimed at *us* still
            # propagates.
            await asyncio.wait({pending})
