"""The limits on spaces: the super admin's caps, and an organisation's own.

`null` is no limit. An organisation's may only be tighter than the caps
(`docs/service-identity.md` §17.4).
"""

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, Text, TIMESTAMP
from sqlalchemy.orm import Mapped

from app.db.sql.models import Base


class PlatformSpaceCaps(Base):
    __tablename__ = 'platform_space_caps'
    __table_args__ = (CheckConstraint('id = 1', name='ck_platform_space_caps_one_row'),)

    id: Mapped[int] = Column(Integer, primary_key=True, default=1)
    max_spaces_per_app: Mapped[int | None] = Column(Integer, nullable=True)
    max_spaces_per_org: Mapped[int | None] = Column(Integer, nullable=True)
    updated_by_user_id: Mapped[str | None] = Column(
        Text, ForeignKey('users.user_id', ondelete='SET NULL'), nullable=True
    )
    updated_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class OrgSpaceQuota(Base):
    __tablename__ = 'org_space_quotas'

    org_id: Mapped[str] = Column(Text, ForeignKey('organizations.id', ondelete='CASCADE'), primary_key=True)
    max_spaces_per_app: Mapped[int | None] = Column(Integer, nullable=True)
    max_spaces: Mapped[int | None] = Column(Integer, nullable=True)
    updated_by_user_id: Mapped[str | None] = Column(
        Text, ForeignKey('users.user_id', ondelete='SET NULL'), nullable=True
    )
    updated_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
