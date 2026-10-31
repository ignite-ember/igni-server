"""An organisation's quota of time its producers' VMs spend awake.

Set by a super admin. Stored, not enforced yet: nothing counts awake
time until the node agent reports it.
"""

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, ForeignKey, Text, TIMESTAMP
from sqlalchemy.orm import Mapped

from app.db.sql.models import Base


class OrgVmQuota(Base):
    __tablename__ = 'org_vm_quotas'

    org_id: Mapped[str] = Column(Text, ForeignKey('organizations.id', ondelete='CASCADE'), primary_key=True)
    #: VM-seconds awake per calendar month, summed over the
    #: organisation's producers; null is unlimited.
    awake_vm_seconds_per_month: Mapped[int | None] = Column(BigInteger, nullable=True)
    updated_by_user_id: Mapped[str | None] = Column(
        Text, ForeignKey('users.user_id', ondelete='SET NULL'), nullable=True
    )
    updated_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
