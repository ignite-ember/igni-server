"""What a request owes after its answer — a usage row and a node's slot — run to the end."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any


class TailRunner:
    """Runs a request's tail in its own task, so a cancellation stops the wait and not the work.

    Long-lived (one per app): the event loop keeps only a weak reference
    to a task, so this holds the strong one until the task is done.
    """

    def __init__(self) -> None:
        self._tails: set[asyncio.Task[None]] = set()

    async def run(self, work: Coroutine[Any, Any, None]) -> None:
        """Run *work* to its end even if the caller is cancelled while it runs.

        Starlette cancels a response whose client left, and delivers that
        again at every await inside its cancel scope; the shield is what
        lets the usage write and the release finish anyway.
        """
        task = asyncio.ensure_future(work)
        self._tails.add(task)
        task.add_done_callback(self._tails.discard)
        await asyncio.shield(task)
