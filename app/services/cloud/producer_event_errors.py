"""Why an item is refused, or a producer's own events are not theirs to fetch.

`ProducerEventRefusals` decides what HTTP makes of each.
"""

from __future__ import annotations

from app.api.base_v1.schema.producer_events import MAX_PAYLOAD_BYTES, MAX_VIEW_BYTES


class EventRefused(Exception):
    """The item, or the request about items, cannot go ahead; the message is the caller's to read."""


class ExpiryPast(EventRefused):
    """The service asked for an `expires_at` that has already passed."""

    def __init__(self) -> None:
        super().__init__('expires_at is already past')


class PayloadTooLarge(EventRefused):
    """The item's payload, serialised, is over `MAX_PAYLOAD_BYTES`."""

    def __init__(self) -> None:
        super().__init__(f'A payload may be at most {MAX_PAYLOAD_BYTES} bytes')


class ViewTooLarge(EventRefused):
    """An action's view, serialised, is over `MAX_VIEW_BYTES`."""

    def __init__(self) -> None:
        super().__init__(f'A view may be at most {MAX_VIEW_BYTES} bytes')


class ExtensionOff(EventRefused):
    """The producer does not have the service's extension on."""

    def __init__(self, extension_id: str) -> None:
        super().__init__(f'{extension_id} is not on for this producer')


class NoSuchEvent(EventRefused):
    """No event of this id from the calling service — another service's included."""

    def __init__(self) -> None:
        super().__init__('No such event from this service')


class NotAPerson(EventRefused):
    """Only a signed-in person in an organisation fetches their own events; an app's go through the gateway."""

    def __init__(self) -> None:
        super().__init__('Only a person fetches their own events here')
