"""Telling a connected client that its queue moved.

The read-side counterpart to :class:`~app.services.notifications.nudge.Nudger`:
that one wakes whoever is listening, this one *is* the listening.

**It carries no notification.** A frame says "there is something newer
for you" and the client answers by reading the list, exactly as the
comment stream does — see :mod:`app.services.comment_notifier` for why
that bargain is the whole design. A client that misses fifty frames
reads the list once on reconnect and is as correct as one that
received every frame. The stream decides how soon, never whether.

Which is why the subscription, the two timings and the frame format
live here rather than in the router. They are one decision about how
long a connection is worth holding open and what to put on it, and a
route handler that owned them would own it by accident.
"""

from __future__ import annotations

import asyncio
import time

from app.services import comment_notifier
from app.services.comment_notifier import CommentNotifier

#: What an idle stream sends so an intermediary does not close it.
#: A comment, not an event: EventSource ignores it, and it costs one
#: line rather than a spurious "go and read the list".
KEEPALIVE_FRAME = ': keepalive\n\n'

#: There is something newer; go and read the list.
EVENT_WAITING = 'waiting'
#: This connection has been open long enough. Come back.
EVENT_RECONNECT = 'reconnect'


class NotificationStream:
    """Opens one listening connection per account that asks for one.

    Constructor-injected with the notifier rather than reading the
    module global from inside the loop, the way :class:`Nudger` is, so
    that a test hands in a recording fake instead of patching a global.
    """

    def __init__(
        self,
        *,
        notifier: CommentNotifier | None = None,
        keepalive_seconds: int = comment_notifier.KEEPALIVE_INTERVAL,
        max_seconds: int = comment_notifier.STREAM_MAX_SECONDS,
    ) -> None:
        self._notifier = notifier or CommentNotifier()
        self._keepalive_seconds = keepalive_seconds
        self._max_seconds = max_seconds

    @classmethod
    def default(cls) -> NotificationStream:
        """The instance a route gets from ``Depends``."""
        return cls()

    @property
    def headers(self) -> dict[str, str]:
        """What an SSE response has to say to get through a proxy."""
        return {
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            # Nginx buffers by default, which turns a live stream into
            # a batch delivered when the buffer fills.
            'X-Accel-Buffering': 'no',
        }

    def listen(self, *, user_id: str, cursor: str = '') -> NotificationChannel:
        """One connection's worth of listening, as a context manager."""
        return NotificationChannel(
            notifier=self._notifier,
            user_id=user_id,
            cursor=cursor,
            keepalive_seconds=self._keepalive_seconds,
            max_seconds=self._max_seconds,
        )


class NotificationChannel:
    """One client, listening, for as long as it is worth holding open.

    A context manager because the unsubscribe is the point: a queue
    left in the notifier after the connection is gone is a slot that
    fills up and then drops nudges for the connection that replaced it.
    """

    def __init__(
        self,
        *,
        notifier: CommentNotifier,
        user_id: str,
        cursor: str,
        keepalive_seconds: int,
        max_seconds: int,
    ) -> None:
        self._notifier = notifier
        self._user_id = user_id
        self._cursor = cursor
        self._keepalive_seconds = keepalive_seconds
        self._max_seconds = max_seconds
        self._queue: asyncio.Queue[str] | None = None
        self._started = 0.0

    def __enter__(self) -> NotificationChannel:
        self._queue = self._notifier.subscribe(self._user_id)
        self._started = time.monotonic()
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._queue is not None:
            self._notifier.unsubscribe(self._user_id, self._queue)
            self._queue = None

    def opening(self) -> str:
        """The first frame, sent before anything has happened.

        So a client that has just connected reads the list without
        waiting for the next thing to happen — which may be tomorrow.
        """
        return self._frame(self._cursor, EVENT_WAITING)

    def spent(self) -> bool:
        """Whether this connection has been open long enough.

        Ending one deliberately is cheaper than discovering a half-open
        one: EventSource reconnects on its own and replays from
        ``Last-Event-ID``, so a reconnect costs one request and loses
        nothing.
        """
        return time.monotonic() - self._started > self._max_seconds

    def reconnect(self) -> str:
        """The last frame: come back on a fresh connection."""
        return self._frame(self._cursor, EVENT_RECONNECT)

    async def waiting(self) -> str:
        """The next frame, or a keepalive when nothing happened in time."""
        if self._queue is None:
            raise RuntimeError('listen to a channel before reading from it')
        try:
            cursor = await asyncio.wait_for(self._queue.get(), timeout=self._keepalive_seconds)
        except asyncio.TimeoutError:
            return KEEPALIVE_FRAME
        return self._frame(cursor, EVENT_WAITING)

    def _frame(self, cursor: str, event: str) -> str:
        return f'event: {event}\nid: {cursor}\ndata: 1\n\n'
