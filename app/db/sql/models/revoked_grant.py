"""A person's sign-in at a client, ended by that client (RFC 7009).

Refresh tokens are signed, not stored: igni checks one by its signature
and by who the person still is, and keeps nothing per token. That is
enough for everything igni decides — a person removed, deactivated or
withdrawn is refused at the next renewal — but not for a *client*
saying "this person signed out of me": nothing about the person changed.

So the sign-in itself has an id. The authorization-code grant mints it,
every refresh token issued from that sign-in carries it (`grant`), and
revoking any one of them writes the id here. Revoking the grant rather
than the one token matters because renewing does not retire the token
it renews: every earlier refresh token of the same sign-in is still a
valid signature, and a denylist of one `jti` would leave all of them
working.

A row is needed only until the last refresh token it could stop has
expired — `expires_at`, at most a refresh token's lifetime after the
revocation — and is pruned after that.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, ForeignKey, Index, Text, TIMESTAMP
from sqlalchemy.orm import Mapped

from app.db.sql.models import Base


class RevokedGrant(Base):
    __tablename__ = 'revoked_grants'
    __table_args__ = (Index('idx_revoked_grants_expires_at', 'expires_at'),)

    #: The `grant` claim every refresh token of the sign-in carries.
    grant_id: Mapped[str] = Column(Text, primary_key=True)
    #: The client the sign-in was at, and which revoked it.
    client_id: Mapped[str] = Column(
        Text, ForeignKey('registered_clients.client_id', ondelete='CASCADE'), nullable=False
    )
    revoked_at: Mapped[datetime] = Column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    #: When no refresh token of this sign-in can still be valid.
    expires_at: Mapped[datetime] = Column(TIMESTAMP(timezone=True), nullable=False)
