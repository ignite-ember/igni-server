"""VmProfileRequest model — what an app or an extension asks its VMs to get, until an org admin decides.

A VM runs nothing as root that an org admin has not approved, and a
profile's packages and setup script are exactly that. So an app (with its
own token) or an extension (through its manifest) only **asks**: the
request is kept here, one per app and one per extension in each
organisation, and changes no VM. An org admin approves it — it becomes a
profile, assigned to the app — or rejects it. A changed request is
pending again; the profile keeps what was last approved
(`docs/service-identity.md` §12.3).
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import CheckConstraint, Column, ForeignKey, Text, TIMESTAMP, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped

from app.db.sql.models import TenantBase

PENDING = 'pending'
APPROVED = 'approved'
REJECTED = 'rejected'


class VmProfileRequest(TenantBase):
    __tablename__ = 'vm_profile_requests'
    __table_args__ = (
        UniqueConstraint('subject_kind', 'subject_id', name='uq_vm_profile_requests_subject'),
        CheckConstraint("subject_kind IN ('app', 'extension')", name='ck_vm_profile_requests_subject_kind'),
        CheckConstraint("state IN ('pending', 'approved', 'rejected')", name='ck_vm_profile_requests_state'),
    )

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    #: `app` or `extension`.
    subject_kind: Mapped[str] = Column(Text, nullable=False)
    #: The app's id, or the extension's manifest id.
    subject_id: Mapped[str] = Column(Text, nullable=False)
    #: Everything asked for, every value filled in: exactly what would run.
    settings: Mapped[dict[str, object]] = Column(JSONB, nullable=False)
    #: A digest of `settings`; an approval names it.
    version: Mapped[str] = Column(Text, nullable=False)
    state: Mapped[str] = Column(Text, nullable=False, default=PENDING)
    #: `app` or `service`, and its client id.
    requested_by_kind: Mapped[str] = Column(Text, nullable=False)
    requested_by_id: Mapped[str] = Column(Text, nullable=False)
    requested_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    #: The admin who decided, and as what (`org_admin`/`super_admin`).
    decided_by_user_id: Mapped[str | None] = Column(Text, nullable=True)
    decided_as: Mapped[str | None] = Column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    reason: Mapped[str | None] = Column(Text, nullable=True)
    #: The profile an approval made; it holds what was last approved.
    profile_id: Mapped[str | None] = Column(Text, ForeignKey('vm_profiles.id', ondelete='SET NULL'), nullable=True)
