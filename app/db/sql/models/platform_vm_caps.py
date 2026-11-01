"""The super admin's limits on every VM profile of the deployment.

One row (`id` = 1). A profile is refused when it asks for more; one made
before a cap was lowered is served clamped to it.
"""

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, Text, text, TIMESTAMP
from sqlalchemy.orm import Mapped

from app.db.sql.models import Base


class PlatformVmCaps(Base):
    __tablename__ = 'platform_vm_caps'
    __table_args__ = (CheckConstraint('id = 1', name='ck_platform_vm_caps_one_row'),)

    id: Mapped[int] = Column(Integer, primary_key=True, default=1)
    max_memory_mib: Mapped[int] = Column(Integer, nullable=False)
    max_vcpus: Mapped[int] = Column(Integer, nullable=False)
    #: The longest `idle_before_pause` a profile may have; null allows
    #: any, including never.
    max_idle_before_pause_s: Mapped[int | None] = Column(Integer, nullable=True)
    #: The largest `volume_max_gib` a profile may have.
    max_volume_gib: Mapped[int] = Column(Integer, nullable=False, server_default=text('200'), default=200)
    #: The longest `erase_after_idle_days` a profile may have; null allows
    #: any, including never. Set, a profile must erase within it.
    max_erase_after_idle_days: Mapped[int | None] = Column(Integer, nullable=True)
    updated_by_user_id: Mapped[str | None] = Column(
        Text, ForeignKey('users.user_id', ondelete='SET NULL'), nullable=True
    )
    updated_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
