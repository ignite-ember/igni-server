"""Organization invitation — email-based invite with expiry token."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Column, Index, Text, TIMESTAMP

from app.db.sql.models import Base


class OrgInvitation(Base):
    __tablename__ = 'org_invitations'
    __table_args__ = (
        Index('idx_org_invitations_org_email', 'org_id', 'email', unique=True),
        Index('idx_org_invitations_token', 'token', unique=True),
        Index('idx_org_invitations_email', 'email'),
    )

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    org_id = Column(Text, nullable=False)
    email = Column(Text, nullable=False)
    role = Column(Text, nullable=False, default='member')
    token = Column(Text, nullable=False, unique=True)
    invited_by = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default='pending')  # pending | accepted | expired | revoked
    expires_at = Column(TIMESTAMP(timezone=True), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
