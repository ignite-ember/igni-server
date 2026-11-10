"""ProducerFeedEntry model — the last state of each producer the gateway's feed reported.

One row per producer of the deployment, on the shared plane: whether it
is active, the VM profile it gets, and that profile's `version` and
`packages_version` (`docs/service-identity.md` §12). When what igni server computes
for a producer differs from its row, the row is rewritten with the next
`seq`, and that is what the gateway's feed lists (§11.3).

`seq` comes from `producer_feed_seq` and is only ever drawn by the one
transaction holding the feed's advisory lock, which commits before it
lets go — so a number is never committed after a larger one, and a
reader paging by `seq` misses nothing.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Sequence,
    Text,
    text,
    TIMESTAMP,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped

from app.db.sql.models import Base, TenantBase

#: The kind of producer a person is, beside `APP` and `SPACE`.
PERSON = 'person'

#: The numbers the feed is ordered by.
FEED_SEQ = Sequence('producer_feed_seq', metadata=Base.metadata)


class ProducerFeedEntry(Base):
    __tablename__ = 'producer_feed'
    __table_args__ = (
        PrimaryKeyConstraint('org_id', 'producer_kind', 'producer_id', 'incarnation', name='pk_producer_feed'),
        Index('uq_producer_feed_seq', 'seq', unique=True),
        Index('idx_producer_feed_org', 'org_id', 'producer_kind'),
    )

    #: No foreign key: the row must outlive a deleted organisation, so
    #: the gateway learns its producers are gone.
    org_id: Mapped[str] = Column(Text, nullable=False)
    producer_kind: Mapped[str] = Column(Text, nullable=False)
    producer_id: Mapped[str] = Column(Text, nullable=False)
    #: Which life of the producer: a person's membership id (new each time
    #: they are added to the organisation), an app's or a space's own id.
    #: A new incarnation is a new VM; the old one's row is removed.
    incarnation: Mapped[str] = Column(Text, nullable=False)
    #: A person: an active member, not deactivated. An app: registered,
    #: with the cloud enabled (a working VM credential).
    active: Mapped[bool] = Column(Boolean, nullable=False)
    #: The profile last computed for it; kept as it was once it is gone.
    profile_version: Mapped[str] = Column(Text, nullable=False)
    packages_version: Mapped[str] = Column(Text, nullable=False)
    #: The profile itself, as its VM gets it (`EffectiveVmProfile`): the
    #: gateway wakes it from this, never asking igni server. Null only on
    #: a row last written before the feed carried it.
    vm_profile: Mapped[dict[str, object] | None] = Column(JSONB, nullable=True)
    #: The producer's volume in GiB (§12.1): the profile's, or more where
    #: an admin grew it.
    volume_gib: Mapped[int] = Column(Integer, nullable=False)
    #: A space's app, which owns it; null for a person or an app.
    owner_app_id: Mapped[str | None] = Column(Text, nullable=True)
    #: When igni server saw the change.
    changed_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    seq: Mapped[int] = Column(BigInteger, FEED_SEQ, nullable=False)
    #: Removed by its owner — a space deleted, an app deleted, an account
    #: deleted or erased, an organisation deleted: the gateway erases its
    #: VM's volume and keys. Never set back: a removed producer stays so.
    removed: Mapped[bool] = Column(Boolean, nullable=False, server_default=text('false'), default=False)
    #: When it was removed.
    removed_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    #: Days between removal and erasure: the organisation's retention for
    #: a person removed from it, 0 for anything else.
    erase_after_days: Mapped[int | None] = Column(Integer, nullable=True)
    #: When the gateway erases the VM's state, volume and keys; null while
    #: a legal hold is on. Only set on a removed row.
    erase_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    #: When the gateway was handed this row's latest version removed; reset
    #: whenever the row changes. Purged `PRODUCER_FEED_RETENTION_DAYS`
    #: after `erase_at`, once handed over.
    seen_removed_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    #: The person was erased: purged as soon as `erase_at` has passed and
    #: the gateway was handed it, without waiting out the retention.
    erased: Mapped[bool] = Column(Boolean, nullable=False, server_default=text('false'), default=False)
    #: A legal hold stands on the producer: on the person in this
    #: organisation, or on the app (which covers its spaces). The gateway
    #: erases nothing of the VM for inactivity while it does (§12.2, §18.2).
    legal_hold: Mapped[bool] = Column(Boolean, nullable=False, server_default=text('false'), default=False)
    #: When the gateway reported this removed incarnation's erasure done:
    #: its node state, volume, keys, replicas and rows gone (§18.3).
    erasure_done_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    #: Discarded by an org admin (§18.5): the app's name at the time, who
    #: asked and when. A person discarded now keeps their own name.
    discarded_label: Mapped[str | None] = Column(Text, nullable=True)
    discarded_by_user_id: Mapped[str | None] = Column(Text, nullable=True)
    discarded_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)


class ProducerFeedMark(Base):
    """A producer whose state may have changed, written by trigger with the change itself.

    `producer_id` is `*` for every producer of the kind in the
    organisation, and `app:<app_id>` (kind `space`) for every space of an
    app; `org_id` `*` with kind `*` is every producer of the deployment.
    `n` counts the marks, so the feed forgets only the ones it has read.
    """

    __tablename__ = 'producer_feed_marks'
    __table_args__ = (PrimaryKeyConstraint('org_id', 'producer_kind', 'producer_id', name='pk_producer_feed_marks'),)

    org_id: Mapped[str] = Column(Text, nullable=False)
    producer_kind: Mapped[str] = Column(Text, nullable=False)
    producer_id: Mapped[str] = Column(Text, nullable=False)
    n: Mapped[int] = Column(BigInteger, nullable=False, server_default=text('1'), default=1)


class TenantProducerFeedMark(TenantBase):
    """The same, in an organisation's own database, for the changes made there."""

    __tablename__ = 'producer_feed_marks'
    __table_args__ = (PrimaryKeyConstraint('producer_kind', 'producer_id', name='pk_producer_feed_marks'),)

    producer_kind: Mapped[str] = Column(Text, nullable=False)
    producer_id: Mapped[str] = Column(Text, nullable=False)
    n: Mapped[int] = Column(BigInteger, nullable=False, server_default=text('1'), default=1)
