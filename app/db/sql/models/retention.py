"""How long a removed member's VM is kept before it is erased, and legal holds that stop the clock.

An organisation — the controller of its members' work — sets
`removed_member_retention_days` (0 erases at once), within the maximum
the super admin sets for the platform. A legal hold an org admin puts on
a person keeps every removed incarnation of theirs in that organisation
from being erased until it is released (`docs/service-identity.md` §18).
"""

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, PrimaryKeyConstraint, Text, text, TIMESTAMP
from sqlalchemy.orm import Mapped

from app.db.sql.models import Base

#: Until an organisation sets its own.
DEFAULT_REMOVED_MEMBER_RETENTION_DAYS = 30
#: Until the super admin sets the platform's maximum.
DEFAULT_MAX_REMOVED_MEMBER_RETENTION_DAYS = 365


class PlatformRetention(Base):
    __tablename__ = 'platform_retention'
    __table_args__ = (CheckConstraint('id = 1', name='ck_platform_retention_one_row'),)

    id: Mapped[int] = Column(Integer, primary_key=True, default=1)
    max_removed_member_retention_days: Mapped[int] = Column(
        Integer, nullable=False, server_default=text('365'), default=DEFAULT_MAX_REMOVED_MEMBER_RETENTION_DAYS
    )
    updated_by_user_id: Mapped[str | None] = Column(
        Text, ForeignKey('users.user_id', ondelete='SET NULL'), nullable=True
    )
    updated_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class OrgRetention(Base):
    __tablename__ = 'org_retention'

    org_id: Mapped[str] = Column(Text, ForeignKey('organizations.id', ondelete='CASCADE'), primary_key=True)
    removed_member_retention_days: Mapped[int] = Column(
        Integer, nullable=False, server_default=text('30'), default=DEFAULT_REMOVED_MEMBER_RETENTION_DAYS
    )
    updated_by_user_id: Mapped[str | None] = Column(
        Text, ForeignKey('users.user_id', ondelete='SET NULL'), nullable=True
    )
    updated_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class LegalHold(Base):
    """A person's removed incarnations in one organisation are not erased while this stands."""

    __tablename__ = 'legal_holds'
    __table_args__ = (PrimaryKeyConstraint('org_id', 'user_id', name='pk_legal_holds'),)

    #: No foreign keys: a hold outlives the membership — that is its point.
    org_id: Mapped[str] = Column(Text, nullable=False)
    user_id: Mapped[str] = Column(Text, nullable=False)
    reason: Mapped[str] = Column(Text, nullable=False)
    placed_by_user_id: Mapped[str] = Column(Text, nullable=False)
    placed_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
