"""ApiKey model — a personal API key: a person's access, handed to a program.

A person makes one on the portal and gives it to a program — a script, a
CI job, a service of their own — which exchanges it at the token endpoint
for a short-lived session as that person (`docs/service-identity.md` §22).
Static, unlike a VM credential: a program keeps one key until it expires
or somebody revokes it. Only a hash of the secret is kept; the key itself
is shown once, when it is made.
"""

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Column, func, Index, Text, text, TIMESTAMP
from sqlalchemy.orm import Mapped

from app.db.sql.models import TenantBase

#: Why a key stopped working before it expired.
REVOKED_BY_PERSON = 'revoked_by_person'
REVOKED_BY_ADMIN = 'revoked_by_admin'
SIGNED_OUT = 'signed_out_or_removed'
REVOKED_REASONS = (REVOKED_BY_PERSON, REVOKED_BY_ADMIN, SIGNED_OUT)


class ApiKey(TenantBase):
    __tablename__ = 'api_keys'
    __table_args__ = (
        Index('ix_api_keys_user_working', 'user_id', postgresql_where=text('revoked_at IS NULL')),
        CheckConstraint(
            "revoked_reason IS NULL OR revoked_reason IN ('revoked_by_person', 'revoked_by_admin', "
            "'signed_out_or_removed')",
            name='ck_api_keys_revoked_reason',
        ),
    )

    #: `secrets.token_hex(16)`; the key names it, after the organisation.
    id: Mapped[str] = Column(Text, primary_key=True)
    user_id: Mapped[str] = Column(Text, nullable=False)
    #: What the person called it: which program has it.
    name: Mapped[str] = Column(Text, nullable=False)
    #: SHA-256 of the secret. The key itself is never stored.
    secret_hash: Mapped[str] = Column(Text, nullable=False)
    created_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = Column(TIMESTAMP(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = Column(TIMESTAMP(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = Column(Text, nullable=True)
    revoked_by_id: Mapped[str | None] = Column(Text, nullable=True)
