"""Authenticating a client by a JWT it signed (`private_key_jwt`).

RFC 7523 §2.2, as OIDC Core §9 profiles it. The client signs a short
assertion — `iss` and `sub` its client id, `aud` this server, a `jti`
never used before — with the key it registered. Nothing secret crosses
the wire, and an assertion seen in transit is useless a moment later.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.redis.authorization_codes import SpentAssertionDB
from app.db.sql.models.registered_client import APPROVED, PENDING, RegisteredClient
from app.db.sql.registered_clients import RegisteredClientDB
from app.security import jose
from app.services.identity.errors import OAuthError
from app.services.identity.jwk import JwkSet, PublicJwk

ASSERTION_TYPE = 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer'

#: The longest an assertion may claim to live. A client mints one per
#: token request, so anything longer is only a longer replay window.
MAX_ASSERTION_SECONDS = 300


class ClientAssertions:
    def __init__(self, *, clients: RegisteredClientDB | None = None, spent: SpentAssertionDB | None = None) -> None:
        self._clients = clients or RegisteredClientDB()
        self._spent = spent or SpentAssertionDB()

    async def authenticate(
        self, *, assertion_type: str | None, assertion: str | None, audiences: set[str]
    ) -> RegisteredClient:
        """The approved client that signed `assertion`, or `invalid_client`."""
        if assertion_type != ASSERTION_TYPE or not assertion:
            raise OAuthError.invalid_client(f'authenticate with client_assertion_type={ASSERTION_TYPE}')

        try:
            unverified = jose.unverified_claims(assertion)
            header = jose.unverified_header(assertion)
        except jose.TokenError as exc:
            raise OAuthError.invalid_client('the client assertion is not a JWT') from exc

        client_id = str(unverified.get('iss') or '')
        if not client_id or unverified.get('sub') != client_id:
            raise OAuthError.invalid_client('the assertion must have iss and sub both set to the client id')

        client = await self._clients.get(client_id)
        if client is None:
            raise OAuthError.invalid_client('no such client')
        if client.state == PENDING:
            raise OAuthError.invalid_client('this client is waiting for a super admin to approve it')
        if client.state != APPROVED:
            raise OAuthError.invalid_client('this client has been revoked')

        key = self._key_for(client, str(header.get('kid') or ''))
        claims = self._verified(assertion, key, client_id=client_id, audiences=audiences)

        lifetime = int(claims['exp']) - int(datetime.now(timezone.utc).timestamp())
        if lifetime > MAX_ASSERTION_SECONDS:
            raise OAuthError.invalid_client(f'an assertion may live at most {MAX_ASSERTION_SECONDS} seconds')
        if not await self._spent.spend(client_id=client_id, jti=str(claims['jti']), ttl_seconds=lifetime):
            raise OAuthError.invalid_client('this assertion has already been used')
        return client

    @staticmethod
    def _key_for(client: RegisteredClient, kid: str) -> PublicJwk:
        keys = JwkSet.model_validate(client.jwks)
        if kid:
            found = keys.find(kid)
            if found is None:
                raise OAuthError.invalid_client(f'no registered key has kid {kid!r}')
            return found
        if len(keys.keys) == 1:
            return keys.keys[0]
        raise OAuthError.invalid_client('this client has several keys; name one with kid')

    @staticmethod
    def _verified(assertion: str, key: PublicJwk, *, client_id: str, audiences: set[str]) -> dict[str, Any]:
        try:
            claims: dict[str, Any] = jose.decode(
                assertion,
                key.as_dict(),
                algorithms=[key.algorithm],
                issuer=client_id,
                require=('exp', 'jti'),
                verify_aud=False,
            )
        except jose.TokenError as exc:
            raise OAuthError.invalid_client(f'the client assertion did not verify: {exc}') from exc
        audience = claims.get('aud')
        named = {audience} if isinstance(audience, str) else set(audience or [])
        if not named & audiences:
            raise OAuthError.invalid_client("the assertion's aud must be this server's issuer or token endpoint")
        return claims
