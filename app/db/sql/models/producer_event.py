"""ProducerEvent model — an item a service asked igni to deliver to its extension for one producer.

An item is an `event`, a `message`, a new `chat` or an `action` on a chat
(`kind`); every kind is stored, leased, acknowledged and expired the
same way. `thread` is the service's key for the chat it goes to.

Stored until delivered: the gateway fetches a producer's pending events
when it wakes their VM and acknowledges each once the extension has it.
Each row is also the record of who asked: the service, its extension,
the producer, and when (`docs/service-identity.md` §15).

An undelivered event waits at most `SERVICE_EVENT_MAX_WAIT_SECONDS`
(14 days); its service may set an earlier `expires_at`. Then it is
`expired`. Its service reads the `state` — `pending`, `delivered`,
`expired`, or `dropped` with a `dropped_reason` — and the retention round
deletes it `SERVICE_EVENT_RETENTION_DAYS` after it left `pending`.
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, func, Index, Text, text as sql_text, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped

from app.db.sql.models import TenantBase

PENDING = 'pending'
DELIVERED = 'delivered'
#: Its `expires_at` passed before it was delivered.
EXPIRED = 'expired'
#: Not delivered, and never will be; `dropped_reason` says why.
DROPPED = 'dropped'
#: The gateway gave up delivering it; `failed_reason` is its reason.
FAILED = 'failed'
#: An event the producer's igni handed to its extension, and no handler
#: took; `not_handled_reason` says why. Nothing was posted for it.
NOT_HANDLED = 'not_handled'

#: The producer no longer has the extension on.
EXTENSION_OFF = 'extension_switched_off'
#: The producer is gone: a space its app deleted.
PRODUCER_REMOVED = 'producer_removed'


class ProducerEvent(TenantBase):
    __tablename__ = 'producer_events'
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'delivered', 'not_handled', 'expired', 'dropped', 'failed')",
            name='ck_producer_events_state',
        ),
        CheckConstraint("kind IN ('event', 'message', 'chat', 'action')", name='ck_producer_events_kind'),
        Index('idx_producer_events_client', 'client_id', 'updated_at'),
        Index('idx_producer_events_pending', 'producer_kind', 'producer_id', 'state'),
        Index(
            'uq_producer_events_key',
            'extension_id',
            'producer_kind',
            'producer_id',
            'event_key',
            unique=True,
        ),
    )

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    #: The service that asked — a registered client on the shared plane.
    client_id: Mapped[str] = Column(Text, nullable=False)
    #: The extension it is for: the one that service ships, recorded by
    #: igni rather than taken from the request.
    extension_id: Mapped[str] = Column(Text, nullable=False)
    producer_kind: Mapped[str] = Column(Text, nullable=False)
    producer_id: Mapped[str] = Column(Text, nullable=False)
    #: `event`, `message`, `chat` or `action`.
    kind: Mapped[str] = Column(Text, nullable=False, default='event', server_default='event')
    #: An event's type, the extension's own word; for any other kind, the kind.
    event_type: Mapped[str] = Column(Text, nullable=False)
    payload: Mapped[dict[str, object]] = Column(JSONB, nullable=False, default=dict)
    #: The service's key for the chat it goes to; null: the extension's own chat.
    thread: Mapped[str | None] = Column(Text, nullable=True)
    #: A message's text, or a new chat's first message.
    text: Mapped[str | None] = Column(Text, nullable=True)
    #: The title of the chat it opens.
    title: Mapped[str | None] = Column(Text, nullable=True)
    #: A message: start a run, or wait for the chat's next one.
    start_run: Mapped[bool] = Column(Boolean, nullable=False, default=True, server_default=sql_text('true'))
    #: An action's `{op, view, title}` (`ChatAction`).
    action: Mapped[dict[str, object] | None] = Column(JSONB, nullable=True)
    #: The ids of the attachments it names, in order.
    attachment_ids: Mapped[list[str]] = Column(
        JSONB, nullable=False, default=list, server_default=sql_text("'[]'::jsonb")
    )
    #: The same key for one extension and producer is stored once.
    event_key: Mapped[str | None] = Column(Text, nullable=True)
    state: Mapped[str] = Column(Text, nullable=False, default=PENDING)
    created_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=func.now()
    )
    #: Not handed out after this; null waits until delivered.
    expires_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    #: Handed to the gateway, not yet acknowledged; handed out again
    #: after a lease if never acknowledged.
    handed_out_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    #: Why it was dropped, when it was.
    dropped_reason: Mapped[str | None] = Column(Text, nullable=True)
    #: Why the gateway gave up delivering it, when it did.
    failed_reason: Mapped[str | None] = Column(Text, nullable=True)
    #: Why no handler took the event, when none did.
    not_handled_reason: Mapped[str | None] = Column(Text, nullable=True)
    #: When its state last changed — the cursor a service polls with.
    updated_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=func.now()
    )
