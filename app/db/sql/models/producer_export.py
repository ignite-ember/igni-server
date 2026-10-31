"""ProducerExport model — an export an org admin asked for of a removed member's data, during retention.

The node agent runs igni's `db export-producer` for the incarnation
against the organisation's igni database and uploads the archive; it is
kept here, in a large object, until it expires
(`docs/service-identity.md` §18.4).
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import BigInteger, CheckConstraint, Column, Index, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import OID
from sqlalchemy.orm import Mapped

from app.db.sql.models import TenantBase

REQUESTED = 'requested'
HANDED_OUT = 'handed_out'
READY = 'ready'
FAILED = 'failed'


class ProducerExport(TenantBase):
    __tablename__ = 'producer_exports'
    __table_args__ = (
        CheckConstraint("state IN ('requested', 'handed_out', 'ready', 'failed')", name='ck_producer_exports_state'),
        Index('idx_producer_exports_state', 'state'),
    )

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    producer_kind: Mapped[str] = Column(Text, nullable=False)
    producer_id: Mapped[str] = Column(Text, nullable=False)
    incarnation: Mapped[str] = Column(Text, nullable=False)
    state: Mapped[str] = Column(Text, nullable=False, default=REQUESTED)
    requested_by_user_id: Mapped[str] = Column(Text, nullable=False)
    requested_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    #: The node agent it was handed to; handed out again after a lease.
    handed_to: Mapped[str | None] = Column(Text, nullable=True)
    handed_out_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    error: Mapped[str | None] = Column(Text, nullable=True)
    size: Mapped[int | None] = Column(BigInteger, nullable=True)
    #: The archive; unlinked by trigger when the row goes.
    content_oid: Mapped[int | None] = Column(OID, nullable=True)
    #: The archive is deleted then.
    expires_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
