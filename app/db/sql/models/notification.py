"""Something happened, and one person needs to know.

A notification is generic: igni does not know what an extension's
``kind`` means, only who it is for, which extension it came from, and
where in that extension to go. ``docs/service-identity.md`` §10.1 is the
contract: an extension's *service* says what happened and who it
concerns, and igni server resolves the people, stores one row per
person, wakes their machines, and records delivery and reading.

**The source is igni's word, not the caller's.** ``source`` is the
extension the calling service ships, looked up from its upload
(``service_extensions``), so one service cannot post as another.
``source_client_id`` says which registration that was, so the row stays
explainable after a service re-registers.

**Denormalised on purpose.** The actor's address and the title are
copied in rather than joined out. A notification is a record of a
moment — "Sam commented on Runbook" stays true after Sam leaves and the
document is renamed.

**Read, not deleted.** ``delivered_at`` records that the person's
machine listed it, ``read_at`` that they saw it. Deleting instead would
make "what did I already look at" unanswerable the first time two
machines are open.
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Column, func, Index, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped

from app.db.sql.models import TenantBase

#: The partial unique index that decides "already told". The migration
#: owns the on-disk name; the insert names it as its conflict target.
UNIQUE_EVENT_INDEX = 'uq_notifications_event'


class Notification(TenantBase):
    __tablename__ = 'notifications'
    __table_args__ = (
        # The hot path: "what is waiting on me", newest first, and the
        # unread count. ``read_at`` is in the index because the unread
        # rows are what the badge is made of.
        Index('idx_notifications_for_user', 'user_id', 'read_at', 'created_at'),
        # One notification per person per event of one extension. A
        # service retrying, or two code paths deciding the same person
        # should hear about the same thing, must not read as two things
        # having happened. Per source, so one extension's keys cannot
        # swallow another's.
        #
        # Partial: a notification with no ``event_key`` can recur.
        Index(
            UNIQUE_EVENT_INDEX,
            'user_id',
            'source',
            'event_key',
            unique=True,
            postgresql_where=Column('event_key').isnot(None),
        ),
    )

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))

    #: Who needs to know: an active member of this organisation when it
    #: was written.
    user_id: Mapped[str] = Column(Text, nullable=False)

    #: The extension it came from (its manifest id, e.g. ``igni.artifacts``),
    #: recorded by igni from the calling service's upload.
    source: Mapped[str] = Column(Text, nullable=False)
    #: The registered client that posted it.
    source_client_id: Mapped[str] = Column(Text, nullable=False)

    #: The extension's own word for what happened. Free text: igni does
    #: not interpret it.
    kind: Mapped[str] = Column(Text, nullable=False)
    #: What it is about.
    title: Mapped[str] = Column(Text, nullable=False)
    #: One line to read, or nothing when the title says it all.
    body: Mapped[str | None] = Column(Text, nullable=True)

    #: Who did it. The address is copied so the line reads without a
    #: join and keeps reading after the account is gone.
    actor_user_id: Mapped[str | None] = Column(Text, nullable=True)
    actor_email: Mapped[str | None] = Column(Text, nullable=True)

    #: Where to go: ``{"page": <one of the extension's pages>, "params": {...}}``.
    target: Mapped[dict[str, object] | None] = Column(JSONB, nullable=True)

    #: What happened, named so it cannot be told twice. See the partial
    #: unique index above.
    event_key: Mapped[str | None] = Column(Text, nullable=True)

    created_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    #: When the person's machine first listed it.
    delivered_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    #: When it was seen. Null means it is still waiting.
    read_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
