"""A client ending a person's sign-in at igni (RFC 7009) — how signing out of a service ends it here too."""

from __future__ import annotations

from datetime import datetime, timezone
import logging

from pydantic import BaseModel, ConfigDict, ValidationError

from app.db.sql.revoked_grants import RevokedGrantDB
from app.security import jose
from app.services.auth.jwt_service import JWTService, TokenUse
from app.services.identity.errors import OAuthError
from app.services.identity.token_clients import TokenClients
from app.services.identity.token_requests import REFRESH_LIFETIME, RevocationRequest

logger = logging.getLogger(__name__)


class StatedUse(BaseModel):
    """The `token_use` a token claims, read unverified. Anything not an igni use fails validation."""

    model_config = ConfigDict(extra='ignore')

    token_use: TokenUse | None = None


class SignInRevocation:
    def __init__(
        self,
        *,
        clients: TokenClients | None = None,
        tokens: JWTService | None = None,
        revoked: RevokedGrantDB | None = None,
    ) -> None:
        self._tokens = tokens or JWTService()
        self._clients = clients or TokenClients(tokens=self._tokens)
        self._revoked = revoked or RevokedGrantDB()

    async def revoke(self, request: RevocationRequest) -> None:
        """End the sign-in a refresh token belongs to.

        Every refresh token of that sign-in stops working, not only the
        one presented: renewing never retired the earlier ones. A token
        that is not valid at all — expired, unsigned, garbage — is
        answered like a revoked one (§2.2), since there is nothing left
        to stop; one issued to another client is refused, and an access
        token is `unsupported_token_type`: it cannot be revoked, only
        outlived.
        """
        client = await self._clients.authenticate(request)
        if not request.token:
            raise OAuthError.invalid_request('token is required')
        payload = self._tokens.verify(request.token, audience=self._tokens.issuer, use=TokenUse.REFRESH)
        if payload is None:
            if self._names_another_use(request.token):
                raise OAuthError(
                    'unsupported_token_type',
                    'only refresh tokens can be revoked; an access token expires within ten minutes',
                )
            return
        if payload.azp != client.client_id:
            raise OAuthError.invalid_grant('this refresh token was not issued to this client')
        grant_id = payload.grant or ''
        if not grant_id:
            return
        await self._revoked.revoke(
            grant_id=grant_id,
            client_id=client.client_id,
            # Every refresh token of this sign-in was issued no later
            # than now, so none outlives a refresh token's lifetime from now.
            until=datetime.now(timezone.utc) + REFRESH_LIFETIME,
        )
        logger.info('Client %s revoked a sign-in for %s', client.client_id, payload.sub)

    @staticmethod
    def _names_another_use(token: str) -> bool:
        """Whether `token` says it is some other kind of igni token.

        Read unverified, and only to choose which answer to give: a
        refresh token that verified never reaches here.
        """
        try:
            stated = StatedUse.model_validate(jose.unverified_claims(token))
        except (jose.TokenError, ValidationError):
            return False
        return stated.token_use is not None and stated.token_use != TokenUse.REFRESH
