"""A space's token: what an app exchanges its own for, and what a space's VM credential buys.

`docs/service-identity.md` §17.2. Signed in one place so the two routes
to it cannot drift apart.
"""

from __future__ import annotations

from datetime import timedelta

from app.db.sql.models.app_space import AppSpace, SPACE
from app.db.sql.registered_clients import RegisteredClientDB
from app.db.sql.spaces import SpacesDB
from app.services.auth.jwt_service import JWTService, TokenUse
from app.services.auth.token_claims import CustomClaims


class SpaceTokens:
    def __init__(self, tokens: JWTService | None = None) -> None:
        self._tokens = tokens or JWTService()

    @staticmethod
    async def live(org_id: str, space_id: str, *, app_id: str | None = None) -> AppSpace | None:
        """The space, while it is declared and its app is registered (and is *app_id*, when named)."""
        space = await SpacesDB().get(org_id, space_id)
        if space is None or (app_id is not None and space.app_id != app_id):
            return None
        client = await RegisteredClientDB().app(org_id, space.app_id)
        return space if client is not None and client.state == 'approved' else None

    def sign(self, org_id: str, space: AppSpace, *, lifetime: timedelta, audience: str | None = None) -> str:
        """For igni itself, or — exchanged for a service (§9) — for that service's URL."""
        return self._tokens.sign(
            subject=space.id,
            audience=audience or self._tokens.issuer,
            use=TokenUse.SPACE,
            lifetime=lifetime,
            claims=CustomClaims(org=org_id, kind=SPACE, app=space.app_id, external_id=space.external_id),
        )
