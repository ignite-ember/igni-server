"""Waking the organisation-deletion round now rather than at its next interval.

A deletion happens on the request path; the round that delivers its
notices runs in the background (`OrganisationDeletions`). This is the one
thing they share: the round arms it while it runs, a deletion nudges it,
and a nudge with no round running does nothing — the next round, in this
replica or another, delivers the notice anyway.
"""

from __future__ import annotations

import asyncio

from app.utils.singleton import SingletonMeta


class DeletionWake:
    """The signal between a deletion and the round that delivers it."""

    def __init__(self) -> None:
        self._event: asyncio.Event | None = None

    def arm(self) -> asyncio.Event:
        """A fresh event for a round that is starting; what `nudge` sets from now on."""
        self._event = asyncio.Event()
        return self._event

    def disarm(self) -> None:
        """The round has stopped: nudges go nowhere."""
        self._event = None

    def nudge(self) -> None:
        """Run a round now rather than at the next interval. A no-op without a round."""
        if self._event is not None:
            self._event.set()


class ProcessDeletionWake(DeletionWake, metaclass=SingletonMeta):
    """The process's one :class:`DeletionWake`.

    A singleton, like `ProcessSigningKeys` and for the same reason: the
    round is started by the app's lifespan, and the deletions that nudge
    it run in request handlers that have no app in hand to carry an
    instance.
    """
