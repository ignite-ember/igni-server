"""User roles — RBAC layer for the portal.

The first user to log in is auto-promoted to admin.
"""

from datetime import datetime, timezone
from enum import StrEnum
import uuid

from sqlalchemy import Column, Index, Text, TIMESTAMP
from sqlalchemy.orm import Mapped

from app.db.sql.models import Base


class Role(StrEnum):
    ADMIN = 'admin'
    MEMBER = 'member'


class UserRole(Base):
    __tablename__ = 'user_roles'
    __table_args__ = (Index('idx_user_roles_user', 'user_id', unique=True),)

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = Column(Text, nullable=False, unique=True)
    email: Mapped[str | None] = Column(Text, nullable=True)
    pending_email: Mapped[str | None] = Column(Text, nullable=True)
    email_verification_token: Mapped[str | None] = Column(Text, nullable=True)
    full_name: Mapped[str | None] = Column(Text, nullable=True)
    role: Mapped[str] = Column(Text, nullable=False, default=Role.MEMBER.value)
    status: Mapped[str] = Column(Text, nullable=False, default='active')
    assigned_by: Mapped[str | None] = Column(Text, nullable=True)
    created_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
