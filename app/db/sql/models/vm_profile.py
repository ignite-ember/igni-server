"""VmProfile model — what a producer's microVM is given, defined by the org admin.

The org admin defines named profiles and assigns one to each group and
each app; a person gets their group's. A group or app with none
assigned gets the organisation's default profile (`is_default`, one per
organisation, made on first use). The gateway gets each producer's
profile on the producers feed, with a `profile_version` that changes
whenever anything in it does (`docs/service-identity.md` §11.3, §12).
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, Column, func, Index, Integer, Text, text, TIMESTAMP, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped

from app.db.sql.models import TenantBase


class VmProfile(TenantBase):
    __tablename__ = 'vm_profiles'
    __table_args__ = (
        UniqueConstraint('name', name='uq_vm_profiles_name'),
        Index('uq_vm_profiles_one_default', 'is_default', unique=True, postgresql_where=text('is_default')),
    )

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = Column(Text, nullable=False)
    is_default: Mapped[bool] = Column(Boolean, nullable=False, server_default=text('false'), default=False)
    #: The most memory the VM may have, its size at boot. A change
    #: applies at the producer's next cold boot.
    memory_mib: Mapped[int] = Column(Integer, nullable=False)
    #: What the VM normally has; the node's balloon holds the rest back
    #: until the VM needs it. A change applies at once.
    normal_memory_mib: Mapped[int] = Column(Integer, nullable=False, server_default=text('1024'), default=1024)
    vcpus: Mapped[int] = Column(Integer, nullable=False)
    #: Seconds idle before the VM is paused; null is never.
    idle_before_pause_s: Mapped[int | None] = Column(Integer, nullable=True)
    #: What the node expects the VM to use, for placing it; null: the
    #: node's own rule (a share of `memory_mib`).
    expected_memory_mib: Mapped[int | None] = Column(Integer, nullable=True)
    #: `{"apt": [...], "pip": [...], "npm": [...]}`, built into the
    #: profile's read-only layer.
    packages: Mapped[dict[str, list[str]]] = Column(JSONB, nullable=False, default=dict)
    #: Run as root when the profile's layer is built; null for none.
    setup_script: Mapped[str | None] = Column(Text, nullable=True)
    #: A new VM's volume, in GiB, and how far an org admin may grow one.
    volume_gib: Mapped[int] = Column(Integer, nullable=False, server_default=text('10'), default=10)
    volume_max_gib: Mapped[int] = Column(Integer, nullable=False, server_default=text('50'), default=50)
    created_by_user_id: Mapped[str | None] = Column(Text, nullable=True)
    updated_by_user_id: Mapped[str | None] = Column(Text, nullable=True)
    created_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=func.now()
    )
    updated_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=func.now()
    )
