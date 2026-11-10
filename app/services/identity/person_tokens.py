"""The `user` tokens a person's sign-in produces, and the refresh token that renews them."""

from __future__ import annotations

import uuid

from app.db.sql.models.registered_client import RegisteredClient
from app.services.auth.jwt_service import JWTService, TokenUse
from app.services.auth.token_claims import CustomClaims
from app.services.identity.people_in_organisations import PersonInOrganisation
from app.services.identity.token_requests import ACCESS_LIFETIME, IssuedTokens, REFRESH_LIFETIME


class PersonTokens:
    def __init__(self, tokens: JWTService | None = None) -> None:
        self._tokens = tokens or JWTService()

    def access(self, audience: str, person: PersonInOrganisation, *, azp: str | None = None) -> str:
        return self._tokens.sign(
            subject=person.user_id,
            audience=audience,
            use=TokenUse.USER,
            lifetime=ACCESS_LIFETIME,
            claims=person.claims(azp=azp),
        )

    def for_client(self, client: RegisteredClient, person: PersonInOrganisation, *, grant_id: str) -> IssuedTokens:
        """An access token for the client's URL, and a refresh token bound to the client.

        The refresh token names its sign-in (`grant`), which is what a
        revocation stops, and has an id of its own (`jti`).
        """
        refresh = self._tokens.sign(
            subject=person.user_id,
            audience=self._tokens.issuer,
            use=TokenUse.REFRESH,
            lifetime=REFRESH_LIFETIME,
            claims=CustomClaims(org=person.org_id, azp=client.client_id, grant=grant_id, jti=uuid.uuid4().hex),
        )
        return IssuedTokens.access(self.access(client.base_url, person, azp=client.client_id), refresh_token=refresh)
