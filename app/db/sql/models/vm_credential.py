"""VmCredential model — a producer's VM's own long-lived credential.

The platform never mints a token for a producer. Instead each producer's
VM holds a credential of its own, for audience `igni-vm`, which it
exchanges at the token endpoint for a short-lived igni token and a
rotated credential. One row is one **family**: every rotation of one
issued credential. A person's is issued when they sign in through the
gateway; an app's when an org admin enables the cloud for it, and its
first credential waits here, encrypted, for the node agent to pick up
once (`docs/service-identity.md` §16).

Only hashes of the secrets are kept, never a usable credential — except
an app's first one, until it is picked up.
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, Column, func, Index, Integer, Text, text, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped

from app.db.sql.models import TenantBase

#: The only audience a VM credential has.
IGNI_VM = 'igni-vm'

#: Why a family stopped working.
REPLACED = 'replaced'
REUSED = 'reused'
IDLE = 'idle'
SIGNED_OUT = 'signed_out_or_removed'
APP_REMOVED = 'app_removed'
SPACE_REMOVED = 'space_removed'
REVOKED_BY_PERSON = 'revoked_by_person'
REVOKED_BY_ADMIN = 'revoked_by_admin'


class VmCredential(TenantBase):
    __tablename__ = 'vm_credentials'
    __table_args__ = (
        # One working family per producer: issuing another replaces it.
        Index(
            'uq_vm_credentials_one_active',
            'producer_kind',
            'producer_id',
            unique=True,
            postgresql_where=text('revoked_at IS NULL'),
        ),
    )

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    producer_kind: Mapped[str] = Column(Text, nullable=False)
    producer_id: Mapped[str] = Column(Text, nullable=False)
    audience: Mapped[str] = Column(Text, nullable=False, default=IGNI_VM)
    #: How many times it has rotated; part of the credential itself.
    generation: Mapped[int] = Column(Integer, nullable=False, default=0)
    #: SHA-256 of the current secret.
    secret_hash: Mapped[str] = Column(Text, nullable=False)
    #: `[[generation, hash], ...]` of the last rotated-away secrets: one of
    #: them presented again is reuse, and revokes the family.
    previous_hashes: Mapped[list[list[object]]] = Column(JSONB, nullable=False, default=list)
    #: When it last rotated, and whether the one retry of that rotation
    #: (a lost reply, within 60 s) has been spent.
    rotated_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    retry_used: Mapped[bool] = Column(Boolean, nullable=False, server_default=text('false'), default=False)
    #: An app's first credential, encrypted, until the node agent picks it up.
    #: `info['encrypted']` puts it in `tools/rotate_secret_key.py`'s reach:
    #: a test fails if a marked column is missing from the tool.
    pending_pickup: Mapped[str | None] = Column(Text, nullable=True, info={'encrypted': True})
    picked_up_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    #: The platform client that picked it up.
    picked_up_by: Mapped[str | None] = Column(Text, nullable=True)
    created_by_kind: Mapped[str] = Column(Text, nullable=False)
    created_by_id: Mapped[str] = Column(Text, nullable=False)
    created_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = Column(Text, nullable=True)
    revoked_by_id: Mapped[str | None] = Column(Text, nullable=True)
