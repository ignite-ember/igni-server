"""A service or app that may obtain igni tokens.

One mechanism for three kinds. A **service** (CodeIndex, Artifacts) is
part of the deployment: it holds per-organisation data and is told about
erasure and subject-access requests. A **platform** client (the cloud's
gateway and node agent) serves every organisation and acts for none. An
**app** (a review bot, an integration) is a producer of one organisation
(`org_id`), registered there by an org admin, and acts only as itself.
Every kind generated its own key pair and sent the public half; nothing
secret was ever handed to it.

A service or platform registration starts `pending` and does nothing
until a super admin approves it, having compared `thumbprint` with what
the client printed at start-up. An app is approved by the org admin who
registers it. Revoking or removing a client stops every token request
at once, and the tokens it already holds expire within minutes.
"""

from datetime import datetime, timezone
import uuid

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped

from app.db.sql.models import Base

SERVICE = 'service'
APP = 'app'
PLATFORM = 'platform'

PENDING = 'pending'
APPROVED = 'approved'
REVOKED = 'revoked'


class RegisteredClient(Base):
    __tablename__ = 'registered_clients'
    __table_args__ = (
        Index('idx_registered_clients_thumbprint', 'thumbprint', unique=True),
        Index('idx_registered_clients_state', 'state'),
        Index('idx_registered_clients_org', 'org_id'),
        CheckConstraint("kind IN ('service', 'app', 'platform')", name='ck_registered_clients_kind'),
        CheckConstraint("(kind = 'app') = (org_id IS NOT NULL)", name='ck_registered_clients_app_has_org'),
    )

    client_id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    kind: Mapped[str] = Column(Text, nullable=False)
    name: Mapped[str] = Column(Text, nullable=False)
    #: Where igni reaches the service — the subject-access and erasure
    #: fan-out. Empty for an app, which holds nothing to erase.
    base_url: Mapped[str] = Column(Text, nullable=False, default='')
    #: The organisation an app belongs to; null for a service or a
    #: platform client, which are the deployment's.
    org_id: Mapped[str | None] = Column(Text, ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True)
    #: Exact-match list for the authorization-code flow.
    redirect_uris: Mapped[list[str]] = Column(JSONB, nullable=False, default=list)
    #: The client's public keys, as a JWK set.
    jwks: Mapped[dict[str, object]] = Column(JSONB, nullable=False)
    #: Of the first key in `jwks`; what a super admin compares.
    thumbprint: Mapped[str] = Column(Text, nullable=False)
    state: Mapped[str] = Column(Text, nullable=False, default=PENDING)
    created_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    decided_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    decided_by_user_id: Mapped[str | None] = Column(
        Text, ForeignKey('users.user_id', ondelete='SET NULL'), nullable=True
    )
