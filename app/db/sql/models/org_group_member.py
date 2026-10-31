"""OrgGroupMember model — a user assigned to exactly one org group."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Column, ForeignKey, func, Index, Text, TIMESTAMP, UniqueConstraint
from sqlalchemy.orm import Mapped

from app.db.sql.models import TenantBase


class OrgGroupMember(TenantBase):
    __tablename__ = 'org_group_members'
    __table_args__ = (
        Index('idx_org_group_members_group', 'group_id'),
        # Exactly one group per person: the row always exists (the
        # Default group until an admin moves them), and putting somebody
        # in a group is an upsert on this constraint. It is also the
        # index every lookup by person uses.
        UniqueConstraint('user_id', name='uq_org_group_members_user_one_group'),
    )

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    group_id: Mapped[str] = Column(Text, ForeignKey('org_groups.id', ondelete='CASCADE'), nullable=False)
    user_id: Mapped[str] = Column(Text, nullable=False)
    # ``SET NULL`` and nullable: this records who made the row, and the
    # row has to outlive them. With ``NO ACTION`` and ``NOT NULL``,
    # deleting an account raised a foreign-key violation the moment its
    # owner had ever created anything — a 500 on "delete my account" for
    # anybody who had done admin work and then left an org that survives.
    # CASCADE would have been worse: an admin closing their account would
    # take the organisation's policy with them.
    added_by_user_id: Mapped[str | None] = Column(Text, nullable=True)
    added_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
