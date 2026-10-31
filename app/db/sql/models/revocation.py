"""Revocation model — tokens a party checking signatures must stop accepting (`docs/service-identity.md` §21).

A row says: every token for this subject issued at or before
`not_before_ms` (Unix milliseconds, `iat_ms <= not_before_ms`) is revoked. `kind` is
`person` (in `org_id`, or every organisation when it is null), `app`,
`space` or `org`. Rows are numbered from `revocation_seq` under the
feed's lock, like the feed, so a reader paging by `seq` misses none.
"""

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, Index, PrimaryKeyConstraint, Sequence, Text, TIMESTAMP
from sqlalchemy.orm import Mapped

from app.db.sql.models import Base

REVOCATION_SEQ = Sequence('revocation_seq', metadata=Base.metadata)


class Revocation(Base):
    __tablename__ = 'revocations'
    __table_args__ = (
        PrimaryKeyConstraint('seq', name='pk_revocations'),
        Index('idx_revocations_org', 'org_id', 'seq'),
    )

    seq: Mapped[int] = Column(BigInteger, REVOCATION_SEQ, nullable=False)
    kind: Mapped[str] = Column(Text, nullable=False)
    org_id: Mapped[str | None] = Column(Text, nullable=True)
    subject_id: Mapped[str] = Column(Text, nullable=False)
    #: The incarnation revoked, when one is (a person's membership).
    incarnation: Mapped[str | None] = Column(Text, nullable=True)
    not_before_ms: Mapped[int] = Column(BigInteger, nullable=False)
    #: `removed`, `inactive`, `sessions_withdrawn`.
    reason: Mapped[str] = Column(Text, nullable=False)
    created_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
