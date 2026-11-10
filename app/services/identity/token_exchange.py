"""Token exchange (RFC 8693): a session, an app's token or a space's traded for a token a service will accept.

No client authentication and no registration needed: the presented token
is the credential, and the result is only ever for the named audience —
the service's URL (`docs/service-identity.md` §9). An app may also trade
its own token for one of its spaces' (§17.2).
"""

from __future__ import annotations

import logging

from app.db.sql.models.registered_client import APP
from app.services.auth.jwt_service import JWTService, TokenUse
from app.services.auth.token_claims import CustomClaims, VerifiedClaims
from app.services.identity.errors import OAuthError
from app.services.identity.people_in_organisations import PeopleInOrganisations
from app.services.identity.person_tokens import PersonTokens
from app.services.identity.registry import ClientRegistry
from app.services.identity.service_url import ServiceUrl
from app.services.identity.space_tokens import SpaceTokens
from app.services.identity.token_requests import (
    ACCESS_LIFETIME,
    ACCESS_TOKEN_TYPE,
    IssuedTokens,
    JWT_TOKEN_TYPE,
    TOKEN_EXCHANGE,
    TokenRequest,
)

logger = logging.getLogger(__name__)


class TokenExchangeGrant:
    grant_type = TOKEN_EXCHANGE

    def __init__(
        self,
        *,
        tokens: JWTService | None = None,
        people: PeopleInOrganisations | None = None,
        person_tokens: PersonTokens | None = None,
        spaces: SpaceTokens | None = None,
        registry: ClientRegistry | None = None,
    ) -> None:
        self._tokens = tokens or JWTService()
        self._people = people or PeopleInOrganisations()
        self._person_tokens = person_tokens or PersonTokens(self._tokens)
        self._spaces = spaces or SpaceTokens(self._tokens)
        self._registry = registry or ClientRegistry()

    async def grant(self, request: TokenRequest) -> IssuedTokens:
        if request.subject_token_type not in (ACCESS_TOKEN_TYPE, JWT_TOKEN_TYPE):
            raise OAuthError.invalid_request(f'subject_token_type must be {ACCESS_TOKEN_TYPE}')
        if request.space is not None:
            return await self._as_space(request)
        claims = self._tokens.decode_token(request.subject_token or '')
        if not claims.user_id:
            return await self._producer_for_service(request)
        # Any service URL, approved or not: the session is the
        # credential, and whoever holds it has everything already.
        # The URL only says which service the token is for.
        audience = self._audience(request)
        org_id = request.organisation or claims.org_id
        if not org_id:
            raise OAuthError.invalid_request('organisation is required: this session is not pinned to one')
        if claims.org_id and org_id != claims.org_id:
            raise OAuthError.invalid_target('this session is pinned to a different organisation')
        person = await self._people.check(claims.user_id, org_id, issued_at=claims.issued_at)
        return IssuedTokens.access(self._person_tokens.access(audience, person), issued_token_type=ACCESS_TOKEN_TYPE)

    async def _producer_for_service(self, request: TokenRequest) -> IssuedTokens:
        """An app's or a space's own token traded for one for a service's URL — as a person's session is (§9).

        The result is the producer's own kind of token (`token_use` `app`
        or `space`, with the same claims), its `aud` the service's URL: a
        service that accepts only people's `user` tokens refuses it.
        """
        token = request.subject_token or ''
        app = self._tokens.verify(token, audience=self._tokens.issuer, use=TokenUse.APP)
        space = None if app else self._tokens.verify(token, audience=self._tokens.issuer, use=TokenUse.SPACE)
        producer = app or space
        if producer is None:
            raise OAuthError.invalid_grant('subject_token must be a valid igni session, app token or space token')
        org_id = producer.org or ''
        if request.organisation and request.organisation != org_id:
            raise OAuthError.invalid_target('this token is for a different organisation')
        audience = self._audience(request)
        access = (
            await self._app_for(org_id, app, audience=audience)
            if app is not None
            else await self._space_for(org_id, producer, audience=audience)
        )
        return IssuedTokens.access(access, issued_token_type=ACCESS_TOKEN_TYPE)

    async def _app_for(self, org_id: str, app: VerifiedClaims, *, audience: str) -> str:
        client = await self._registry.approved(app.sub, kind=APP)
        if client is None or client.org_id != org_id:
            raise OAuthError.invalid_grant('this app is no longer registered')
        return self._tokens.sign(
            subject=client.client_id,
            audience=audience,
            use=TokenUse.APP,
            lifetime=ACCESS_LIFETIME,
            claims=CustomClaims(org=org_id, kind=APP, name=client.name),
        )

    async def _space_for(self, org_id: str, space_claims: VerifiedClaims, *, audience: str) -> str:
        space = await self._spaces.live(org_id, space_claims.sub, app_id=space_claims.app or '')
        if space is None:
            raise OAuthError.invalid_grant('this space no longer exists')
        return self._spaces.sign(org_id, space, lifetime=ACCESS_LIFETIME, audience=audience)

    async def _as_space(self, request: TokenRequest) -> IssuedTokens:
        """An app's token traded for one of its spaces': the app acting as that end user's producer."""
        payload = self._tokens.verify(request.subject_token or '', audience=self._tokens.issuer, use=TokenUse.APP)
        if payload is None:
            raise OAuthError.invalid_grant("subject_token must be the app's own token")
        org_id = payload.org or ''
        space = await self._spaces.live(org_id, request.space or '', app_id=payload.sub)
        if space is None:
            raise OAuthError.invalid_grant('no such space of this app')
        logger.info('App %s acts as its space %s', space.app_id, space.id)
        return IssuedTokens.access(
            self._spaces.sign(org_id, space, lifetime=ACCESS_LIFETIME), issued_token_type=ACCESS_TOKEN_TYPE
        )

    @staticmethod
    def _audience(request: TokenRequest) -> str:
        try:
            return ServiceUrl.normalise(request.audience or '', what='audience')
        except ValueError as exc:
            raise OAuthError.invalid_target(str(exc)) from exc
