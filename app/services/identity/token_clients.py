"""Which registered client is calling the token or revocation endpoint."""

from __future__ import annotations

from app.db.sql.models.registered_client import RegisteredClient
from app.services.auth.jwt_service import JWTService
from app.services.identity.assertions import ClientAssertions
from app.services.identity.token_requests import RevocationRequest, TokenRequest


class TokenClients:
    def __init__(self, *, assertions: ClientAssertions | None = None, tokens: JWTService | None = None) -> None:
        self._assertions = assertions or ClientAssertions()
        self._tokens = tokens or JWTService()

    @property
    def token_endpoint(self) -> str:
        return f'{self._tokens.issuer}/v1/oauth/token'

    @property
    def revocation_endpoint(self) -> str:
        return f'{self._tokens.issuer}/v1/oauth/revoke'

    async def authenticate(self, request: TokenRequest | RevocationRequest) -> RegisteredClient:
        """The client whose assertion signs *request*, for igni or either endpoint."""
        return await self._assertions.authenticate(
            assertion_type=request.client_assertion_type,
            assertion=request.client_assertion,
            audiences={self._tokens.issuer, self.token_endpoint, self.revocation_endpoint},
        )
