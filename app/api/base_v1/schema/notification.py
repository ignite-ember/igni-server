"""What a notification looks like on the wire, both ways.

In: what an extension's service posts to ``POST /v1/service-notifications``
(``docs/service-identity.md`` §10.1). Out: what the igni host on a
person's machine lists, counts and marks read.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.sql.schemas.notification import (
    MAX_KEY,
    MAX_KIND,
    MAX_TITLE,
    NotificationRef,
    NotificationTarget,
)

#: At most this many notifications in one call…
MAX_BATCH = 100
#: …and this many recipients in each.
MAX_RECIPIENTS = 1000
#: The longest identity a recipient or actor is named by.
MAX_IDENTITY = 320


class ServiceNotification(BaseModel):
    """One thing that happened, and who it concerns, as a service says it."""

    #: The extension's own word for what happened.
    kind: str = Field(min_length=1, max_length=MAX_KIND)
    #: What it is about.
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    #: One line; longer is cut, not refused.
    body: str = ''
    #: Who did it: a user id or an address. Never told about it.
    actor: str = Field(default='', max_length=MAX_IDENTITY)
    #: Where to go: one of the calling service's extension pages.
    target: NotificationTarget | None = None
    #: The same event is delivered once per person.
    event_key: str = Field(default='', max_length=MAX_KEY)
    #: User ids or addresses.
    recipients: list[str] = Field(default_factory=list)


class ServiceNotifications(BaseModel):
    notifications: list[ServiceNotification] = Field(default_factory=list)


class Delivered(BaseModel):
    #: Rows written. Fewer than named when somebody is not an active
    #: member, was the actor, or was already told — normal, not an error.
    delivered: int = 0


class NotificationRow(BaseModel):
    """One notification, for the person it is addressed to."""

    id: str
    #: The organisation it happened in: a person in several sees them all.
    org_id: str
    #: The extension it came from.
    source: str
    kind: str
    title: str
    body: str = ''
    #: Who did it: their address, or their user id when that is all
    #: that is known. Empty when nobody did.
    actor: str = ''
    target: NotificationTarget | None = None
    created_at: datetime | None = None
    delivered_at: datetime | None = None
    read_at: datetime | None = None

    @classmethod
    def from_ref(cls, ref: NotificationRef) -> NotificationRow:
        return cls(
            id=ref.id,
            org_id=ref.org_id,
            source=ref.source,
            kind=ref.kind,
            title=ref.title,
            body=ref.body,
            actor=ref.actor_email or ref.actor_user_id,
            target=ref.target,
            created_at=ref.created_at,
            delivered_at=ref.delivered_at,
            read_at=ref.read_at,
        )


class NotificationList(BaseModel):
    notifications: list[NotificationRow] = Field(default_factory=list)


class MarkRead(BaseModel):
    """``{"ids": [...]}``, ``{"source": "..."}``, or ``{}`` for everything."""

    ids: list[str] | None = Field(default=None, max_length=MAX_BATCH * 10)
    source: str = Field(default='', max_length=MAX_KEY)


class NotificationRead(BaseModel):
    marked: int = 0
    #: What is still unread afterwards.
    unread: int = 0
