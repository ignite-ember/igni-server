"""Chat usage log — one row per /chat/completions request."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Column, DateTime, Float, func, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped

from app.db.sql.models import TenantBase


class ChatUsage(TenantBase):
    __tablename__ = 'chat_usage'

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = Column(String(255), nullable=False)
    org_id: Mapped[str | None] = Column(String(255), nullable=True)
    #: For an app's or a space's VM (`user_id` is `app:<id>` / `space:<id>`):
    #: the app — its own id, or the space's — so an app's whole use sums.
    app_id: Mapped[str | None] = Column(String(255), nullable=True)
    model: Mapped[str] = Column(String(255), nullable=False)
    input_tokens: Mapped[int] = Column(Integer, nullable=False, default=0, server_default=text('0'))
    output_tokens: Mapped[int] = Column(Integer, nullable=False, default=0, server_default=text('0'))
    total_tokens: Mapped[int] = Column(Integer, nullable=False, default=0, server_default=text('0'))
    duration: Mapped[float | None] = Column(Float, nullable=True)
    stream: Mapped[int] = Column(Integer, nullable=False, default=0, server_default=text('0'))  # 0=non-stream, 1=stream
    created_at: Mapped[datetime] = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index('ix_chat_usage_user_created', 'user_id', 'created_at'),
        Index('ix_chat_usage_org_created', 'org_id', 'created_at'),
    )
