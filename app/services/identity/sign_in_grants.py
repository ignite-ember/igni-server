"""A person's sign-in to a service's portal: `authorization_code` (+ PKCE) starts it, `refresh_token` renews it.

A refresh is refused once the person has been deactivated, deleted,
removed from the organisation, or has had their sessions withdrawn, and
once the client itself has revoked the sign-in
(`app.services.identity.sign_in_revocation`).
"""

from __future__ import annotations

import uuid

from app.db.redis.authorization_codes import AuthorizationCodeDB
from app.db.sql.revoked_grants import RevokedGrantDB
from app.services.auth.jwt_service import JWTService, TokenUse
from app.services.identity.errors import OAuthError
from app.services.identity.people_in_organisations import PeopleInOrganisations
from app.services.identity.person_tokens import PersonTokens
from app.services.identity.pkce import Pkce
from app.services.identity.token_clients import TokenClients
from app.services.identity.token_requests import AUTHORIZATION_CODE, IssuedTokens, REFRESH_TOKEN, TokenRequest


class AuthorizationCodeGrant:
    grant_type = AUTHORIZATION_CODE

    def __init__(
        self,
        *,
        clients: TokenClients | None = None,
        codes: AuthorizationCodeDB | None = None,
        people: PeopleInOrganisations | None = None,
        person_tokens: PersonTokens | None = None,
    ) -> None:
        self._clients = clients or TokenClients()
        self._codes = codes or AuthorizationCodeDB()
        self._people = people or PeopleInOrganisations()
        self._person_tokens = person_tokens or PersonTokens()

    async def grant(self, request: TokenRequest) -> IssuedTokens:
        client = await self._clients.authenticate(request)
        pending = await self._codes.redeem(request.code or '')
        if pending is None or pending.client_id != client.client_id:
            raise OAuthError.invalid_grant('this code is not valid; it may have expired or been used')
        if request.redirect_uri != pending.redirect_uri:
            raise OAuthError.invalid_grant('redirect_uri does not match the one the code was issued for')
        if not Pkce.matches(request.code_verifier, pending.code_challenge):
            raise OAuthError.invalid_grant('code_verifier does not match the code challenge')
        person = await self._people.check(pending.user_id, pending.org_id, issued_at=pending.session_issued_at)
        # A new sign-in: every refresh token issued from here on carries its id.
        return self._person_tokens.for_client(client, person, grant_id=uuid.uuid4().hex)


class RefreshGrant:
    grant_type = REFRESH_TOKEN

    def __init__(
        self,
        *,
        clients: TokenClients | None = None,
        tokens: JWTService | None = None,
        revoked: RevokedGrantDB | None = None,
        people: PeopleInOrganisations | None = None,
        person_tokens: PersonTokens | None = None,
    ) -> None:
        self._tokens = tokens or JWTService()
        self._clients = clients or TokenClients(tokens=self._tokens)
        self._revoked = revoked or RevokedGrantDB()
        self._people = people or PeopleInOrganisations()
        self._person_tokens = person_tokens or PersonTokens(self._tokens)

    async def grant(self, request: TokenRequest) -> IssuedTokens:
        client = await self._clients.authenticate(request)
        payload = self._tokens.verify(request.refresh_token or '', audience=self._tokens.issuer, use=TokenUse.REFRESH)
        if payload is None or payload.azp != client.client_id:
            raise OAuthError.invalid_grant('this refresh token is not valid for this client')
        grant_id = payload.grant or ''
        if not grant_id or await self._revoked.is_revoked(grant_id):
            raise OAuthError.invalid_grant('this sign-in has been revoked')
        person = await self._people.check(payload.sub, payload.org or '', issued_at=payload.iat)
        return self._person_tokens.for_client(client, person, grant_id=grant_id)
