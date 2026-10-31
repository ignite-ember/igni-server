"""OrgGroupExtension model — an approved extension switched on for a group.

A row means the people in this group get this extension; no row means
they do not. Off is the default, so a newly approved service reaches
nobody until an org admin enables it for a group
(`docs/service-identity.md` §8.3).

Not an `org_group_entries` kind on purpose: an entry is a file igni
materialises from the group pack, and an extension never travels in the
pack — its bundle comes from its service through igni server (§8). What
a group decides here is only *whether* its people get it.
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Column, ForeignKey, func, Index, Text, TIMESTAMP, UniqueConstraint
from sqlalchemy.orm import Mapped

from app.db.sql.models import TenantBase


class OrgGroupExtension(TenantBase):
    __tablename__ = 'org_group_extensions'
    __table_args__ = (
        Index('idx_org_group_extensions_extension', 'extension_id'),
        UniqueConstraint('group_id', 'extension_id', name='uq_org_group_extensions_group_extension'),
    )

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    group_id: Mapped[str] = Column(Text, ForeignKey('org_groups.id', ondelete='CASCADE'), nullable=False)
    #: The manifest id, the same as `service_extensions.extension_id` on
    #: the shared plane — a different database, so no foreign key.
    extension_id: Mapped[str] = Column(Text, nullable=False)
    #: Who switched it on. Nullable and never a foreign key: the row has
    #: to outlive the admin who made it, as `org_group_members` does.
    enabled_by_user_id: Mapped[str | None] = Column(Text, nullable=True)
    enabled_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
