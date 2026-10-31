"""The values :class:`~app.db.sql.notifications.NotificationsDB` speaks in.

:class:`NotificationDraft` goes in — everything a notification says,
already attributed to its source and actor, written once per recipient.
:class:`NotificationRef` comes out — a row flattened off its session.
:class:`NotificationTarget` is the one structured field: where in the
extension to go.

The limits live here because the row is what they protect: a
notification is a line in a list, and nothing about it should be big.
"""

from __future__ import annotations

from datetime import datetime
import json

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.sql.models.notification import Notification

#: How much of a body goes in the line. Enough to recognise which remark
#: it was, short enough that a notification stays one line; the rest is
#: one click away, in the extension.
EXCERPT = 140
#: The longest ``kind`` — a word, like ``comment`` or ``access_requested``.
MAX_KIND = 64
#: The longest title: what it is about, not a paragraph.
MAX_TITLE = 200
#: The longest ``event_key``, and a page name.
MAX_KEY = 200
#: A target's parameters: a handful of route values, not a payload.
MAX_TARGET_PARAMS = 32
MAX_TARGET_BYTES = 2048

ParamValue = str | int | float | bool | None


def _one_line(value: str | None) -> str:
    """Collapse whitespace so a notification never wraps."""
    return ' '.join((value or '').split())


class NotificationTarget(BaseModel):
    """Where in the extension to go: one of its pages, and that page's params."""

    model_config = ConfigDict(frozen=True)

    page: str = Field(min_length=1, max_length=MAX_KEY)
    params: dict[str, ParamValue] = Field(default_factory=dict, max_length=MAX_TARGET_PARAMS)

    @field_validator('params')
    @classmethod
    def _small(cls, value: dict[str, ParamValue]) -> dict[str, ParamValue]:
        if len(json.dumps(value)) > MAX_TARGET_BYTES:
            raise ValueError(f'target params may be at most {MAX_TARGET_BYTES} bytes as JSON')
        return value


class NotificationDraft(BaseModel):
    """Everything a notification says, before it is addressed.

    One draft per thing that happened, written once per recipient.
    ``source`` and ``source_client_id`` are igni's, never the caller's.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    source_client_id: str
    kind: str
    title: str
    body: str = ''
    actor_user_id: str = ''
    actor_email: str = ''
    target: NotificationTarget | None = None
    event_key: str = ''

    @field_validator('title')
    @classmethod
    def _title(cls, value: str) -> str:
        return _one_line(value)[:MAX_TITLE]

    @field_validator('body')
    @classmethod
    def _excerpt(cls, value: str) -> str:
        """One line, capped — enforced on the way in, so it cannot be skipped."""
        words = _one_line(value)
        return words if len(words) <= EXCERPT else f'{words[: EXCERPT - 1]}…'

    def row_for(self, user_id: str, *, notification_id: str, created_at: datetime) -> dict[str, object]:
        """The columns of this draft's row for one recipient."""
        return {
            'id': notification_id,
            'user_id': user_id,
            'source': self.source,
            'source_client_id': self.source_client_id,
            'kind': self.kind,
            'title': self.title,
            'body': self.body or None,
            'actor_user_id': self.actor_user_id or None,
            'actor_email': self.actor_email or None,
            'target': self.target.model_dump(mode='json') if self.target else None,
            'event_key': self.event_key or None,
            'created_at': created_at,
        }


class NotificationRef(BaseModel):
    """A notification that has left the database.

    Built inside the session and answered instead of the row, so nothing
    upstairs holds a SQLAlchemy instance. Nullable text arrives as ``''``.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    #: Whose queue this is in.
    user_id: str
    #: The organisation whose database holds it.
    org_id: str = ''
    source: str
    source_client_id: str
    kind: str
    title: str
    body: str = ''
    actor_user_id: str = ''
    actor_email: str = ''
    target: NotificationTarget | None = None
    event_key: str = ''
    created_at: datetime | None = None
    delivered_at: datetime | None = None
    read_at: datetime | None = None

    @classmethod
    def from_row(cls, row: Notification, *, org_id: str) -> NotificationRef:
        """The row, read from ``org_id``'s database — which the row itself does not say."""
        return cls(
            id=str(row.id),
            user_id=str(row.user_id),
            org_id=org_id,
            source=str(row.source),
            source_client_id=str(row.source_client_id),
            kind=str(row.kind),
            title=str(row.title),
            body=str(row.body or ''),
            actor_user_id=str(row.actor_user_id or ''),
            actor_email=str(row.actor_email or ''),
            target=NotificationTarget.model_validate(row.target) if row.target else None,
            event_key=str(row.event_key or ''),
            created_at=row.created_at,
            delivered_at=row.delivered_at,
            read_at=row.read_at,
        )


class NotificationCounts(BaseModel):
    """What the badges are made of: unread, in all and per extension."""

    unread: int = 0
    by_source: dict[str, int] = Field(default_factory=dict)
