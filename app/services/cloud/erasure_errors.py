"""Why a discard or an erasure report is refused. `ErasureRefusals` decides what HTTP makes of each."""

from __future__ import annotations

from datetime import datetime


class ErasureRefused(Exception):
    """The discard or report cannot go ahead; the message is the caller's to read."""


class LegalHoldStands(ErasureRefused):
    """A legal hold stands on the person: nothing of theirs is erased until it is released."""

    def __init__(self, *, placed_at: datetime, reason: str) -> None:
        super().__init__(
            f'A legal hold stands on this person here (placed {placed_at:%Y-%m-%d}: {reason}); '
            'nothing of theirs is erased until it is released'
        )


class CurrentMemberKept(ErasureRefused):
    """The person is still a member: their VM is never discarded."""

    def __init__(self) -> None:
        super().__init__("A current member's VM is never discarded: it is kept while they are a member")


class NothingToDiscard(ErasureRefused):
    """No removed VM of the person is waiting for its erasure."""

    def __init__(self) -> None:
        super().__init__('No removed VM of this person here is waiting for its erasure')


class NoSuchIncarnation(ErasureRefused):
    """The gateway reported an erasure of an incarnation the feed does not keep."""

    def __init__(self) -> None:
        super().__init__('No such removed incarnation')
