"""Wake a listener when something happens on their documents.

This is a **nudge, not a delivery mechanism**, and the distinction is
the whole design. The stream never carries a comment's contents. It
carries "there is something newer than X for you", and the client
answers by running the sync pull it already has.

That is what makes a dropped connection harmless. Correctness lives
in the cursor: the pull is ordered on ``updated_at``, the projection
is a MERGE on the comment id, and a row naming a version the client
has not fetched is skipped without consuming the cursor. A laptop
that sleeps through fifty notifications wakes up, pulls once, and is
exactly as correct as one that received every single one. The stream
only decides *how soon*, never *whether*.

So this module can afford to be lossy, and is. Waiters are held in
memory on one process, a notify that reaches nobody is dropped, and
nothing is persisted. A deployment running several workers will have
listeners on a process that does not see the write — that costs
those listeners their nudge, and they fall back to their reconnect
interval. Worth knowing before adding a second worker; not worth a
message broker until there is one.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
import logging

from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)

#: Seconds between ``: keepalive`` comments on an idle stream.
#:
#: Eight, matching the chat stream, because the constraint is the
#: same: an idle proxy closes a connection it thinks is dead, and
#: intermediaries commonly draw that line at 30 or 60 seconds.
KEEPALIVE_INTERVAL: int = 8

#: How long a stream lives before asking the client to reconnect.
#:
#: Ending a connection deliberately is cheaper than discovering a
#: half-open one: EventSource reconnects on its own and replays from
#: ``Last-Event-ID``, so a reconnect costs one request and loses
#: nothing. Fifteen minutes keeps a per-account connection from
#: pinning a worker indefinitely.
STREAM_MAX_SECONDS: int = 900


class CommentNotifier(metaclass=SingletonMeta):
    """In-process fan-out, one queue per listening connection.

    One instance, because the writer and the stream have to be looking
    at the same object: threading it from the DB layer to the endpoint
    would put a notification parameter on methods with no other reason
    to know a stream exists.

    `SingletonMeta` rather than the `NOTIFIER = CommentNotifier()` this
    used to end with. Same single instance, reached the same way every
    other shared object in this codebase is reached, and no module
    global holding a mutable map. Callers that want to supply their own
    — `NotificationStream` and `CommentNudge` both take one — are
    unaffected; they were already the injectable half of this.
    """

    def __init__(self) -> None:
        self._waiters: dict[str, set[asyncio.Queue[str]]] = defaultdict(set)

    def subscribe(self, user_id: str) -> asyncio.Queue[str]:
        # Bounded. A client that stops reading must not grow a queue
        # without limit — and because the payload is only a nudge,
        # dropping one when the buffer is full costs nothing: the next
        # one says the same thing, and the pull that follows sees
        # everything either way.
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=8)
        self._waiters[user_id].add(queue)
        return queue

    def unsubscribe(self, user_id: str, queue: asyncio.Queue[str]) -> None:
        waiters = self._waiters.get(user_id)
        if not waiters:
            return
        waiters.discard(queue)
        if not waiters:
            # Don't leave an empty set per user who ever connected.
            self._waiters.pop(user_id, None)

    def notify(self, user_id: str, cursor: str) -> None:
        """Tell one account's listeners there is something newer.

        Never raises and never blocks: it is called from inside a
        request that is doing something more important than this, and
        a failure to nudge must not fail the write that triggered it.
        """
        for queue in list(self._waiters.get(user_id, ())):
            try:
                queue.put_nowait(cursor)
            except asyncio.QueueFull:
                logger.debug('comment notify dropped for %s: listener is behind', user_id)
            except Exception:  # noqa: BLE001 — see the docstring
                # The catch-all is the contract, not laziness: this runs
                # inside a write that matters more than the nudge, and
                # `put_nowait` raising anything other than QueueFull is a
                # defect in this module that still must not roll back
                # somebody's comment. It is logged at warning with the
                # traceback, so it is not hidden either.
                logger.warning('comment notify failed for %s', user_id, exc_info=True)

    def listener_count(self, user_id: str) -> int:
        return len(self._waiters.get(user_id, ()))

    def reset_for_tests(self) -> None:
        """Drop every waiter.

        One process-wide instance means one `_waiters` map shared by
        every test that touches it. The tests used to build their own
        `CommentNotifier()` to stay isolated, which worked and quietly
        meant they were not exercising the object the application uses.
        """
        self._waiters.clear()
