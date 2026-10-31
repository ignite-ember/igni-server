"""A person's notifications: delivered here, read here.

``docs/service-identity.md`` §10.1. An extension's service says what
happened and who it concerns; igni server decides the rest:

* :class:`~app.services.notifications.delivery.NotificationDelivery` —
  resolving the people, attributing the source, writing one row per
  person once per event, and waking them;
* :class:`~app.services.notifications.feed.NotificationFeed` — listing
  (which marks delivered), counting per extension, marking read;
* :class:`~app.services.notifications.stream.NotificationStream` — the
  host's live connection, carrying only a cursor;
* :class:`~app.services.notifications.nudge.Nudger` — waking a listener.
  Lossy on purpose: what is stored is the notification.
"""

from app.services.notifications.delivery import NoExtension, NotAPage, NotificationDelivery
from app.services.notifications.feed import NotificationFeed
from app.services.notifications.nudge import Nudger
from app.services.notifications.stream import NotificationChannel, NotificationStream

__all__ = [
    'NoExtension',
    'NotAPage',
    'NotificationChannel',
    'NotificationDelivery',
    'NotificationFeed',
    'NotificationStream',
    'Nudger',
]
