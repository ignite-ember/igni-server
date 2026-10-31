"""OrgAppExtension model — an approved extension switched on for an app.

The app's own list, separate from every group's: a row means the app
gets the extension; no row means it does not, the default
(`docs/service-identity.md` §8.3, §11).
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Column, ForeignKey, func, Index, Text, TIMESTAMP, UniqueConstraint
from sqlalchemy.orm import Mapped

from app.db.sql.models import TenantBase


class OrgAppExtension(TenantBase):
    __tablename__ = 'org_app_extensions'
    __table_args__ = (
        Index('idx_org_app_extensions_extension', 'extension_id'),
        UniqueConstraint('app_id', 'extension_id', name='uq_org_app_extensions_app_extension'),
    )

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    app_id: Mapped[str] = Column(Text, ForeignKey('org_apps.app_id', ondelete='CASCADE'), nullable=False)
    #: The manifest id, as `service_extensions.extension_id`.
    extension_id: Mapped[str] = Column(Text, nullable=False)
    enabled_by_user_id: Mapped[str | None] = Column(Text, nullable=True)
    enabled_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
