"""Which database holds each organisation's data.

Each organisation has its own logical database — `<db>_t<number>` on the
same Postgres server — so a session opened for one organisation cannot
reach another's rows, even by a query that forgot to filter. This is the
registry that routes to them, and it lives in the shared plane because
routing has to know every organisation.

`number` is the organisation's tenant number, assigned once and never
reused, from which the other services derive their own per-organisation
databases and Redis indexes too. A deleted organisation's row stays,
`detached`: its data is kept, and nothing routes to it.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, Index, Integer, Text, TIMESTAMP
from sqlalchemy.orm import Mapped

from app.db.sql.models import Base

#: Routed to.
ACTIVE = 'active'
#: The organisation was deleted. The data is kept; nothing routes to it.
DETACHED = 'detached'


class Tenant(Base):
    __tablename__ = 'tenants'
    __table_args__ = (
        Index('idx_tenants_number', 'number', unique=True),
        Index('idx_tenants_database', 'database', unique=True),
    )

    org_id: Mapped[str] = Column(Text, primary_key=True)
    number: Mapped[int] = Column(Integer, nullable=False)
    database: Mapped[str] = Column(Text, nullable=False)
    state: Mapped[str] = Column(Text, nullable=False, default=ACTIVE)
    created_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
