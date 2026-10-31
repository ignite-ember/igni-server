"""AppSpace model — a space: one end user of an app, a producer of its own.

The app declares it, by its own name for the user (`external_id`, unique
within the app); igni server gives it an id. It gets its own VM, volume,
keys and database login, its app's VM profile and extensions, and is in
no group (`docs/service-identity.md` §17). Deleted with its app.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, ForeignKey, func, Index, Text, TIMESTAMP, UniqueConstraint
from sqlalchemy.orm import Mapped

from app.db.sql.models import TenantBase

#: The producer kind of a space.
SPACE = 'space'


class AppSpace(TenantBase):
    __tablename__ = 'app_spaces'
    __table_args__ = (
        UniqueConstraint('app_id', 'external_id', name='uq_app_spaces_app_external'),
        Index('idx_app_spaces_app', 'app_id'),
    )

    id: Mapped[str] = Column(Text, primary_key=True)
    app_id: Mapped[str] = Column(Text, ForeignKey('org_apps.app_id', ondelete='CASCADE'), nullable=False)
    #: The app's own name for its user — it may name a person of the app's.
    external_id: Mapped[str] = Column(Text, nullable=False)
    created_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
