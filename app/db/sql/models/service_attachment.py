"""ServiceAttachment model — a file a service uploaded for an item it sends a producer's agent.

Kept in the organisation's own database, as the items are, and never
longer than the item that names it: a day while no item names it, then
the item's `expires_at` (`docs/service-identity.md` §15). The producer's
igni fetches it with its own credential while it delivers the item, and
imports it into the chat as a person's upload would be.
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import BigInteger, Column, Index, LargeBinary, Text, TIMESTAMP
from sqlalchemy.orm import Mapped

from app.db.sql.models import TenantBase


class ServiceAttachment(TenantBase):
    __tablename__ = 'service_attachments'
    __table_args__ = (
        Index('idx_service_attachments_event', 'event_id'),
        Index('idx_service_attachments_expires', 'expires_at'),
    )

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    #: The service that uploaded it; only it may name it on an item.
    client_id: Mapped[str] = Column(Text, nullable=False)
    filename: Mapped[str] = Column(Text, nullable=False)
    content_type: Mapped[str] = Column(Text, nullable=False)
    size: Mapped[int] = Column(BigInteger, nullable=False)
    #: Hex SHA-256 of `content`.
    sha256: Mapped[str] = Column(Text, nullable=False)
    content: Mapped[bytes] = Column(LargeBinary, nullable=False)
    #: The item that names it; null until one does. One item each.
    event_id: Mapped[str | None] = Column(Text, nullable=True)
    created_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[datetime] = Column(TIMESTAMP(timezone=True), nullable=False)
