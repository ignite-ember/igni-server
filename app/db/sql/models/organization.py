"""Organization model — multi-tenant container for teams, billing, repos."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Column, Index, Text, TIMESTAMP
from sqlalchemy.orm import Mapped

from app.db.sql.models import Base


class Organization(Base):
    __tablename__ = 'organizations'
    __table_args__ = (
        Index('idx_organizations_slug', 'slug', unique=True),
        Index('idx_organizations_owner', 'owner_user_id'),
    )

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    slug: Mapped[str] = Column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = Column(Text, nullable=False)
    owner_user_id: Mapped[str] = Column(Text, nullable=False)
    created_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
