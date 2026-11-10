"""`client_credentials`: a client acting as itself, for igni (`aud` = the issuer).

A service names one organisation and gets a `service` token (or, asking
for the `revocations` scope, one naming none); an app gets an `app` token
for the organisation it belongs to; a platform client gets a `platform`
token naming no organisation.
"""

from __future__ import annotations

import logging

from app.db.sql.models.registered_client import APP, PLATFORM, RegisteredClient, SERVICE
from app.db.sql.organizations import OrganizationDB
from app.security.service_caller import REVOCATIONS_SCOPE
from app.services.auth.jwt_service import JWTService, TokenUse
from app.services.auth.token_claims import CustomClaims
from app.services.identity.errors import OAuthError
from app.services.identity.token_clients import TokenClients
from app.services.identity.token_requests import ACCESS_LIFETIME, CLIENT_CREDENTIALS, IssuedTokens, TokenRequest

logger = logging.getLogger(__name__)


class ClientCredentialsGrant:
    grant_type = CLIENT_CREDENTIALS

    def __init__(
        self,
        *,
        clients: TokenClients | None = None,
        tokens: JWTService | None = None,
        organizations: OrganizationDB | None = None,
    ) -> None:
        self._tokens = tokens or JWTService()
        self._clients = clients or TokenClients(tokens=self._tokens)
        self._organizations = organizations or OrganizationDB()

    async def grant(self, request: TokenRequest) -> IssuedTokens:
        client = await self._clients.authenticate(request)
        if client.kind == APP:
            return self._as_app(client, request)
        if client.kind == PLATFORM:
            return self._as_platform(client)
        if client.kind != SERVICE:
            raise OAuthError('unauthorized_client', 'this client cannot act as itself')
        if not request.organisation and request.scope == REVOCATIONS_SCOPE:
            return self._for_revocations(client)
        if not request.organisation:
            raise OAuthError.invalid_request('organisation is required: every token is for one organisation')
        organisation = await self._organizations.get_by_id(request.organisation)
        if organisation is None:
            raise OAuthError.invalid_target('no such organisation')
        logger.info('Issued a service token to %s for organisation %s', client.client_id, organisation.id)
        return self._issue(client, use=TokenUse.SERVICE, claims=CustomClaims(org=str(organisation.id)))

    def _for_revocations(self, client: RegisteredClient) -> IssuedTokens:
        """A service token naming no organisation: only for revocations and the organisations it serves."""
        logger.info('Issued a revocations token to %s', client.client_id)
        return self._issue(client, use=TokenUse.SERVICE, claims=CustomClaims(scope=REVOCATIONS_SCOPE))

    def _as_app(self, client: RegisteredClient, request: TokenRequest) -> IssuedTokens:
        """An app's token: always for the organisation it belongs to, never another."""
        org_id = str(client.org_id or '')
        if request.organisation and request.organisation != org_id:
            raise OAuthError.invalid_target('an app acts only in the organisation it belongs to')
        logger.info('Issued an app token to %s for organisation %s', client.client_id, org_id)
        return self._issue(client, use=TokenUse.APP, claims=CustomClaims(org=org_id, kind=APP, name=client.name))

    def _as_platform(self, client: RegisteredClient) -> IssuedTokens:
        """A platform client's token: for the deployment, naming no organisation."""
        logger.info('Issued a platform token to %s', client.client_id)
        return self._issue(client, use=TokenUse.PLATFORM, claims=None)

    def _issue(self, client: RegisteredClient, *, use: TokenUse, claims: CustomClaims | None) -> IssuedTokens:
        return IssuedTokens.access(
            self._tokens.sign(
                subject=client.client_id,
                audience=self._tokens.issuer,
                use=use,
                lifetime=ACCESS_LIFETIME,
                claims=claims,
            )
        )
