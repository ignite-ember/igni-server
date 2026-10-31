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
    interval: int = KEEPALIVE_INTERVAL,
    stale_timeout: int = STALE_TIMEOUT,
) -> AsyncIterator[str]:
    """Yield upstream lines, with keepalives filling the gaps.

    Raises `UpstreamStale` once *stale_timeout* has passed with nothing
    from the upstream. The clock resets on real data only, which is the
    whole point: a long generation keeps the connection open through
    its own output, and a dead one runs the timer out.
    """
    lines = response.aiter_lines().__aiter__()
    last_data = time.monotonic()

    while True:
        try:
            line = await asyncio.wait_for(lines.__anext__(), timeout=interval)
        except asyncio.TimeoutError:
            if time.monotonic() - last_data > stale_timeout:
                raise UpstreamStale(f'No data from upstream for {stale_timeout}s') from None
            yield KEEPALIVE_LINE
            continue
        except StopAsyncIteration:
            return

        last_data = time.monotonic()
        yield line
