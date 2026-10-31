"""Answer as soon as there is something to say: the feed's and the revocations' long polls.

A reader passes `wait` (seconds, at most 30). The read is tried at once;
with nothing new, the request waits for a `NOTIFY` on the shared plane —
sent by every mark (a trigger, in the transaction that changed a
producer), every feed change and every revocation — and tries again,
waking at least every second for what changed in an organisation's own
database without a notification (`docs/service-identity.md` §11.3, §21).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import time
from typing import TypeVar

from app.db.sql.revocations import FeedSignals

T = TypeVar('T')

#: The longest a reader may ask to wait.
MAX_WAIT_S = 30.0
#: How often a waiting read looks again without a notification.
RECHECK_S = 1.0


class LongPoll:
    @staticmethod
    async def run(fetch: Callable[[], Awaitable[list[T]]], *, wait: float) -> list[T]:
        found = await fetch()
        if found or wait <= 0:
            return found
        deadline = time.monotonic() + min(wait, MAX_WAIT_S)
        async with FeedSignals().listening() as signal:
            while True:
                found = await fetch()
                remaining = deadline - time.monotonic()
                if found or remaining <= 0:
                    return found
                await signal.wait(min(RECHECK_S, remaining))
