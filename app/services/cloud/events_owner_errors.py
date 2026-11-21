"""Why a person's events are not served to the side asking, or their owner cannot be what was asked.

`EventsOwnerRefusals` decides what HTTP makes of each.
"""

from __future__ import annotations

from app.db.sql.models.person_event_owner import EventsOwner


class EventsOwnerRefused(Exception):
    """The request about who pulls a person's events cannot go ahead; the message is the person's to read."""

    #: The stable token a client reads instead of the sentence.
    error: str


class ModeForbidsOwner(EventsOwnerRefused):
    """The person's group runs igni in one place only, and the owner asked for is the other."""

    error = 'mode_forbids'

    def __init__(self, owner: EventsOwner) -> None:
        where = 'in the cloud' if owner == 'cloud' else 'on this computer'
        super().__init__(f'Your organisation does not run igni {where} for your group.')
        self.owner = owner


class EventsOwnedElsewhere(EventsOwnerRefused):
    """The desktop asked for events its person's cloud VM pulls."""

    error = 'events_owned_elsewhere'

    def __init__(self, owner: EventsOwner) -> None:
        super().__init__('Your igni in the cloud receives your events now.')
        self.owner = owner
