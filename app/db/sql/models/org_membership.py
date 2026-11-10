"""Organization membership — ties a user to an org with a role."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Column, Index, Text, text, TIMESTAMP
from sqlalchemy.orm import Mapped

from app.db.sql.models import Base

#: A current member: counts toward the organisation and reaches its data.
ACTIVE = 'active'


class OrgMembership(Base):
    __tablename__ = 'org_memberships'
    __table_args__ = (
        Index('idx_org_memberships_unique', 'org_id', 'user_id', unique=True),
        Index('idx_org_memberships_user', 'user_id'),
        Index('idx_org_memberships_org', 'org_id'),
        Index('idx_org_memberships_org_status', 'org_id', 'status'),
        # A person is in one organisation at a time: the one owning their
        # address's domain (migration 0022). Removal deletes the row.
        Index(
            'uq_org_memberships_one_org_per_person',
            'user_id',
            unique=True,
            postgresql_where=text("status IN ('active', 'deactivated')"),
        ),
        # One pending invitation per address, per org (migration
        # 9f2b7c41ad86). The index above cannot serve this: an invite
        # fabricates its own ``user_id`` (``invited-<random>``), so two
        # invites for one address are two distinct pairs. Partial, because
        # ``invited_email`` is NULL for real members and a *claimed* invite
        # is no longer pending — re-inviting somebody who left must stay
        # possible.
        Index(
            'uq_org_memberships_pending_invite',
            'org_id',
            'invited_email',
            unique=True,
            postgresql_where=text("status = 'invited' AND invited_email IS NOT NULL"),
        ),
    )

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    org_id: Mapped[str] = Column(Text, nullable=False)
    user_id: Mapped[str] = Column(Text, nullable=False)
    role: Mapped[str] = Column(Text, nullable=False, default='member')  # admin | member
    status: Mapped[str] = Column(Text, nullable=False, default='active')  # active | invited | deactivated
    invited_email: Mapped[str | None] = Column(Text, nullable=True)
    invited_by: Mapped[str | None] = Column(Text, nullable=True)
    created_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
