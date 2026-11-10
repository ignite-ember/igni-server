"""Telling a listening client that its queue moved.

The notification is already written by the time anything here runs.
This only decides how soon it is read: a listener on another worker
misses the nudge and finds out on reconnect, which is the bargain
:mod:`app.services.notifications.listeners` was built on.
"""

from __future__ import annotations

import logging

from app.services.notifications.listeners import Listeners

logger = logging.getLogger(__name__)


class Nudger:
    """Wakes whoever is listening, so the news arrives now."""

    def __init__(self, *, notifier: Listeners | None = None) -> None:
        # Resolved once, here, rather than read out of the module from
        # inside a loop. A test hands in a recording fake instead of
        # patching a global.
        self._notifier = notifier or Listeners()

    async def wake(self, user_ids: set[str], *, cursor: str = '') -> None:
        """Nudge each of these accounts. Lossy on purpose.

        What is *stored* is the notification, and a client that misses
        every nudge it was ever sent is exactly as correct after one
        pull as one that received them all. So a failure here is a
        debug line, not an error: it is the nudge, not the news.

        ``cursor`` becomes the stream event's id: the newest
        notification's time, so a reconnecting client says how far it got.
        """
        for user_id in sorted(user_ids):
            try:
                self._notifier.notify(user_id, cursor)
            except Exception as exc:  # noqa: BLE001 — a nudge, not the news
                logger.debug('could not wake %s: %s', user_id, exc)
