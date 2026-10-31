"""The super admin's priority order of extensions.

Two extensions that want one thing — the same prompt, the same section,
the same slash command — collide, and the one higher in this order has
it, automatically, here on the Services page and on every machine. The
same order is the one several extensions' prompt edits stack in and
their hooks are asked in. A super admin sets it once on the
Services page; every machine receives it with the distribution listing.
An extension with no row here ranks below every one that has one, in
the order its service was approved. See `app.services.identity.priority`.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, ForeignKey, Integer, Text, text, TIMESTAMP
from sqlalchemy.orm import Mapped

from app.db.sql.models import Base


class ExtensionPriority(Base):
    __tablename__ = 'extension_priority'

    extension_id: Mapped[str] = Column(Text, primary_key=True)
    #: Highest first.
    position: Mapped[int] = Column(Integer, nullable=False)
    #: The super admin who set the order. Read from `users` when shown,
    #: never copied.
    set_by_user_id: Mapped[str | None] = Column(Text, ForeignKey('users.user_id', ondelete='SET NULL'), nullable=True)
    #: Set with the break-glass key, when nobody could sign in.
    break_glass: Mapped[bool] = Column(Boolean, nullable=False, server_default=text('false'), default=False)
    set_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
