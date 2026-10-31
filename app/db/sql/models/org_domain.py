"""An email domain an organisation owns.

A person belongs to the organisation that owns their address's domain,
and to no other: a self-hosted install holds one or two organisations,
and its people are the company's. So a domain is claimed by at most one
organisation (the unique index), and an organisation may own several
(``acme.com`` and ``acme.co.uk``).
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Column, ForeignKey, Index, Text, TIMESTAMP
from sqlalchemy.orm import Mapped

from app.db.sql.models import Base


class OrgDomain(Base):
    __tablename__ = 'org_domains'
    __table_args__ = (
        Index('idx_org_domains_domain', 'domain', unique=True),
        Index('idx_org_domains_org', 'org_id'),
    )

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    org_id: Mapped[str] = Column(Text, ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False)
    #: Lowercased, no leading ``@``. See ``app.db.sql.org_domains.normalise_domain``.
    domain: Mapped[str] = Column(Text, nullable=False)
    created_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
