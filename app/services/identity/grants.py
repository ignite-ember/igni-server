"""What the token endpoint hands out, and to whom.

Four grants, and every token they produce names one organisation:

* **client_credentials** — a client acting as itself, for igni
  (`aud` = the issuer). A service names one organisation
  (`organisation` is required) and gets a `service` token to call the
  directory with; an app gets an `app` token for the one organisation
  it belongs to (`kind: app`, no `group`); a platform client (the
  cloud's gateway and node agent) gets a `platform` token naming no
  organisation.
* **authorization_code** (+ PKCE) — a person, signed in to a service's
  own portal by way of igni's. The token is for that client's URL only.
* **refresh_token** — renews the above. Refused once the person has
  been deactivated, deleted, removed from the organisation, or has had
  their sessions withdrawn — which is how a revocation reaches a service
  that never calls back — and once the client itself has revoked the
  sign-in (`revoke`, RFC 7009), which is how a person signing out of a
  service ends it at igni too.
* **token-exchange** (RFC 8693) — the CLI trading its igni session for a
  token a service will accept. No client authentication and no
  registration needed: the session is the credential, and the result is
  only ever for the named audience — the service's URL.

Access tokens live `ACCESS_LIFETIME`. That is the whole of revocation
for a service: it does not ask igni whether a person is still allowed on
every request, it holds a token that says so for ten minutes.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import uuid

from fastapi import HTTPException
from jose import jwt, JWTError  # type: ignore[import-untyped]
from pydantic import BaseModel

from app.db.redis.authorization_codes import AuthorizationCodeDB
from app.db.sql.models.registered_client import APP, PLATFORM, RegisteredClient, SERVICE
from app.db.sql.organizations import OrganizationDB
from app.db.sql.revoked_grants import RevokedGrantDB
from app.db.sql.team import TeamDB
from app.security.membership_sessions import MembershipSessions
from app.security.service_caller import REVOCATIONS_SCOPE
from app.security.subject import require_existing_user
from app.services.auth.jwt_service import JWTService, TokenUse
from app.services.identity.assertions import ClientAssertions
from app.services.identity.errors import OAuthError
from app.services.identity.registry import ClientRegistry
from app.services.identity.service_url import ServiceUrl
from app.services.identity.space_tokens import SpaceTokens

logger = logging.getLogger(__name__)

ACCESS_LIFETIME = timedelta(minutes=10)
REFRESH_LIFETIME = timedelta(days=30)

CLIENT_CREDENTIALS = 'client_credentials'
AUTHORIZATION_CODE = 'authorization_code'
REFRESH_TOKEN = 'refresh_token'
TOKEN_EXCHANGE = 'urn:ietf:params:oauth:grant-type:token-exchange'
GRANT_TYPES = [CLIENT_CREDENTIALS, AUTHORIZATION_CODE, REFRESH_TOKEN, TOKEN_EXCHANGE]

ACCESS_TOKEN_TYPE = 'urn:ietf:params:oauth:token-type:access_token'
JWT_TOKEN_TYPE = 'urn:ietf:params:oauth:token-type:jwt'


class TokenRequest(BaseModel):
    """The form a client posts. Which fields matter depends on `grant_type`."""

    grant_type: str
    client_assertion_type: str | None = None
    client_assertion: str | None = None
    organisation: str | None = None
    code: str | None = None
    redirect_uri: str | None = None
    code_verifier: str | None = None
    refresh_token: str | None = None
    subject_token: str | None = None
    subject_token_type: str | None = None
    audience: str | None = None
    #: `urn:igni:params:oauth:grant-type:vm-credential`: the VM's own
    #: credential (`app.services.cloud.vm_credentials`).
    vm_credential: str | None = None
    #: `revocations`: a service token for no organisation, good only for
    #: following revocations and the organisations it serves (§3, §21).
    scope: str | None = None
    #: Token exchange: the space an app acts as, with its own token as
    #: `subject_token` (`docs/service-identity.md` §17.2).
    space: str | None = None


class RevocationRequest(BaseModel):
    """The form a client posts to revoke a token (RFC 7009 §2.1)."""

    token: str = ''
    #: `refresh_token` or `access_token`. Only a hint, and not needed:
    #: every igni token says what it is, so the token decides.
    token_type_hint: str | None = None
    client_assertion_type: str | None = None
    client_assertion: str | None = None


class IssuedTokens(BaseModel):
    access_token: str
    token_type: str = 'Bearer'
    expires_in: int
    refresh_token: str | None = None
    issued_token_type: str | None = None
    #: A VM's next credential, from the two VM grants; the presented one
    #: no longer works.
    vm_credential: str | None = None


class PersonInOrganisation(BaseModel):
    """Who a user token says somebody is. Checked fresh at every issue."""

    user_id: str
    email: str
    name: str
    org_id: str
    org_name: str
    org_role: str
    super_admin: bool

    @classmethod
    async def check(cls, user_id: str, org_id: str, *, issued_at: int | None) -> PersonInOrganisation:
        """The person, still allowed and still in the organisation, or `invalid_grant`."""
        try:
            user = await require_existing_user(user_id, issued_at=issued_at)
        except HTTPException as exc:
            raise OAuthError.invalid_grant('this person can no longer sign in') from exc
        membership = await TeamDB().get_active_member(org_id, user_id)
        organisation = await OrganizationDB().get_by_id(org_id)
        if membership is None or organisation is None:
            raise OAuthError.invalid_grant('this person is not an active member of that organisation')
        if MembershipSessions.predates(membership, issued_at):
            raise OAuthError.invalid_grant('this was signed in under an earlier membership of that organisation')
        return cls(
            user_id=user_id,
            email=str(user.email or ''),
            name=str(user.full_name or ''),
            org_id=org_id,
            org_name=str(organisation.display_name or organisation.slug or ''),
            org_role=str(membership.role),
            super_admin=bool(user.is_super_admin),
        )

    def claims(self) -> dict[str, object]:
        return {
            'org': self.org_id,
            'org_name': self.org_name,
            'org_role': self.org_role,
            'email': self.email,
            'name': self.name,
            'super_admin': self.super_admin,
        }


class Pkce:
    """S256 only. `plain` would put the verifier in the redirect."""

    @staticmethod
    def matches(verifier: str | None, challenge: str) -> bool:
        if not verifier or not 43 <= len(verifier) <= 128:
            return False
        digest = hashlib.sha256(verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b'=').decode() == challenge


class TokenGrants:
    def __init__(
        self,
        *,
        assertions: ClientAssertions | None = None,
        codes: AuthorizationCodeDB | None = None,
        tokens: JWTService | None = None,
    ) -> None:
        self._assertions = assertions or ClientAssertions()
        self._codes = codes or AuthorizationCodeDB()
        self._tokens = tokens or JWTService()

    @property
    def token_endpoint(self) -> str:
        return f'{self._tokens.issuer}/v1/oauth/token'

    @property
    def revocation_endpoint(self) -> str:
        return f'{self._tokens.issuer}/v1/oauth/revoke'

    async def grant(self, request: TokenRequest) -> IssuedTokens:
        if request.grant_type == CLIENT_CREDENTIALS:
            return await self._client_credentials(request)
        if request.grant_type == AUTHORIZATION_CODE:
            return await self._authorization_code(request)
        if request.grant_type == REFRESH_TOKEN:
            return await self._refresh(request)
        if request.grant_type == TOKEN_EXCHANGE:
            return await self._exchange(request)
        raise OAuthError(
            'unsupported_grant_type',
            f'grant_type must be one of {", ".join(GRANT_TYPES)}, or a VM grant (docs/service-identity.md §16)',
        )

    async def revoke(self, request: RevocationRequest) -> None:
        """End the sign-in a refresh token belongs to (RFC 7009).

        Every refresh token of that sign-in stops working, not only the
        one presented: renewing never retired the earlier ones. A token
        that is not valid at all — expired, unsigned, garbage — is
        answered like a revoked one (§2.2), since there is nothing left
        to stop; one issued to another client is refused, and an access
        token is `unsupported_token_type`: it cannot be revoked, only
        outlived.
        """
        client = await self._client(request)
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
        if payload.get('azp') != client.client_id:
            raise OAuthError.invalid_grant('this refresh token was not issued to this client')
        grant_id = str(payload.get('grant') or '')
        if not grant_id:
            return
        user_id = str(payload.get('sub') or '')
        await RevokedGrantDB().revoke(
            grant_id=grant_id,
            client_id=client.client_id,
            # Every refresh token of this sign-in was issued no later
            # than now, so none outlives a refresh token's lifetime from now.
            until=datetime.now(timezone.utc) + REFRESH_LIFETIME,
        )
        logger.info('Client %s revoked a sign-in for %s', client.client_id, user_id)

    @staticmethod
    def _names_another_use(token: str) -> bool:
        """Whether `token` says it is some other kind of igni token.

        Read unverified, and only to choose which answer to give: a
        refresh token that verified never reaches here.
        """
        try:
            use = jwt.get_unverified_claims(token).get('token_use')
        except JWTError:
            return False
        return isinstance(use, str) and use != str(TokenUse.REFRESH) and use in {str(u) for u in TokenUse}

    async def _client(self, request: TokenRequest | RevocationRequest) -> RegisteredClient:
        return await self._assertions.authenticate(
            assertion_type=request.client_assertion_type,
            assertion=request.client_assertion,
            audiences={self._tokens.issuer, self.token_endpoint, self.revocation_endpoint},
        )

    async def _client_credentials(self, request: TokenRequest) -> IssuedTokens:
        client = await self._client(request)
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
        organisation = await OrganizationDB().get_by_id(request.organisation)
        if organisation is None:
            raise OAuthError.invalid_target('no such organisation')
        logger.info('Issued a service token to %s for organisation %s', client.client_id, organisation.id)
        return IssuedTokens(
            access_token=self._tokens.sign(
                subject=client.client_id,
                audience=self._tokens.issuer,
                use=TokenUse.SERVICE,
                lifetime=ACCESS_LIFETIME,
                claims={'org': str(organisation.id)},
            ),
            expires_in=int(ACCESS_LIFETIME.total_seconds()),
        )

    def _for_revocations(self, client: RegisteredClient) -> IssuedTokens:
        """A service token naming no organisation: only for revocations and the organisations it serves."""
        logger.info('Issued a revocations token to %s', client.client_id)
        return IssuedTokens(
            access_token=self._tokens.sign(
                subject=client.client_id,
                audience=self._tokens.issuer,
                use=TokenUse.SERVICE,
                lifetime=ACCESS_LIFETIME,
                claims={'scope': REVOCATIONS_SCOPE},
            ),
            expires_in=int(ACCESS_LIFETIME.total_seconds()),
        )

    def _as_app(self, client: RegisteredClient, request: TokenRequest) -> IssuedTokens:
        """An app's token: always for the organisation it belongs to, never another."""
        org_id = str(client.org_id or '')
        if request.organisation and request.organisation != org_id:
            raise OAuthError.invalid_target('an app acts only in the organisation it belongs to')
        logger.info('Issued an app token to %s for organisation %s', client.client_id, org_id)
        return IssuedTokens(
            access_token=self._tokens.sign(
                subject=client.client_id,
                audience=self._tokens.issuer,
                use=TokenUse.APP,
                lifetime=ACCESS_LIFETIME,
                claims={'org': org_id, 'kind': APP, 'name': client.name},
            ),
            expires_in=int(ACCESS_LIFETIME.total_seconds()),
        )

    def _as_platform(self, client: RegisteredClient) -> IssuedTokens:
        """A platform client's token: for the deployment, naming no organisation."""
        logger.info('Issued a platform token to %s', client.client_id)
        return IssuedTokens(
            access_token=self._tokens.sign(
                subject=client.client_id,
                audience=self._tokens.issuer,
                use=TokenUse.PLATFORM,
                lifetime=ACCESS_LIFETIME,
            ),
            expires_in=int(ACCESS_LIFETIME.total_seconds()),
        )

    async def _authorization_code(self, request: TokenRequest) -> IssuedTokens:
        client = await self._client(request)
        pending = await self._codes.redeem(request.code or '')
        if pending is None or pending.client_id != client.client_id:
            raise OAuthError.invalid_grant('this code is not valid; it may have expired or been used')
        if request.redirect_uri != pending.redirect_uri:
            raise OAuthError.invalid_grant('redirect_uri does not match the one the code was issued for')
        if not Pkce.matches(request.code_verifier, pending.code_challenge):
            raise OAuthError.invalid_grant('code_verifier does not match the code challenge')
        person = await PersonInOrganisation.check(pending.user_id, pending.org_id, issued_at=pending.session_issued_at)
        # A new sign-in: every refresh token issued from here on carries its id.
        return self._for_person(client, person, grant_id=uuid.uuid4().hex)

    async def _refresh(self, request: TokenRequest) -> IssuedTokens:
        client = await self._client(request)
        payload = self._tokens.verify(request.refresh_token or '', audience=self._tokens.issuer, use=TokenUse.REFRESH)
        if payload is None or payload.get('azp') != client.client_id:
            raise OAuthError.invalid_grant('this refresh token is not valid for this client')
        grant_id = str(payload.get('grant') or '')
        if not grant_id or await RevokedGrantDB().is_revoked(grant_id):
            raise OAuthError.invalid_grant('this sign-in has been revoked')
        person = await PersonInOrganisation.check(
            str(payload['sub']), str(payload.get('org') or ''), issued_at=int(payload['iat'])
        )
        return self._for_person(client, person, grant_id=grant_id)

    async def _exchange(self, request: TokenRequest) -> IssuedTokens:
        if request.subject_token_type not in (ACCESS_TOKEN_TYPE, JWT_TOKEN_TYPE):
            raise OAuthError.invalid_request(f'subject_token_type must be {ACCESS_TOKEN_TYPE}')
        if request.space is not None:
            return await self._as_space(request)
        claims = self._tokens.decode_token(request.subject_token or '')
        if not claims.user_id:
            return await self._producer_for_service(request)
        try:
            # Any service URL, approved or not: the session is the
            # credential, and whoever holds it has everything already.
            # The URL only says which service the token is for.
            audience = ServiceUrl.normalise(request.audience or '', what='audience')
        except ValueError as exc:
            raise OAuthError.invalid_target(str(exc)) from exc
        org_id = request.organisation or claims.org_id
        if not org_id:
            raise OAuthError.invalid_request('organisation is required: this session is not pinned to one')
        if claims.org_id and org_id != claims.org_id:
            raise OAuthError.invalid_target('this session is pinned to a different organisation')
        person = await PersonInOrganisation.check(claims.user_id, org_id, issued_at=claims.issued_at)
        issued = IssuedTokens(
            access_token=self._access(audience, person, claims={}),
            expires_in=int(ACCESS_LIFETIME.total_seconds()),
            issued_token_type=ACCESS_TOKEN_TYPE,
        )
        return issued

    async def _producer_for_service(self, request: TokenRequest) -> IssuedTokens:
        """An app's or a space's own token traded for one for a service's URL — as a person's session is (§9).

        The result is the producer's own kind of token (`token_use` `app`
        or `space`, with the same claims), its `aud` the service's URL: a
        service that accepts only people's `user` tokens refuses it.
        """
        token = request.subject_token or ''
        app = self._tokens.verify(token, audience=self._tokens.issuer, use=TokenUse.APP)
        space_claims = None if app else self._tokens.verify(token, audience=self._tokens.issuer, use=TokenUse.SPACE)
        if app is None and space_claims is None:
            raise OAuthError.invalid_grant('subject_token must be a valid igni session, app token or space token')
        org_id = str((app or space_claims or {}).get('org') or '')
        if request.organisation and request.organisation != org_id:
            raise OAuthError.invalid_target('this token is for a different organisation')
        try:
            audience = ServiceUrl.normalise(request.audience or '', what='audience')
        except ValueError as exc:
            raise OAuthError.invalid_target(str(exc)) from exc
        if space_claims is not None:
            space = await SpaceTokens.live(org_id, str(space_claims['sub']), app_id=str(space_claims.get('app') or ''))
            if space is None:
                raise OAuthError.invalid_grant('this space no longer exists')
            access = SpaceTokens(self._tokens).sign(org_id, space, lifetime=ACCESS_LIFETIME, audience=audience)
        else:
            assert app is not None
            client = await ClientRegistry().approved(str(app['sub']), kind=APP)
            if client is None or client.org_id != org_id:
                raise OAuthError.invalid_grant('this app is no longer registered')
            access = self._tokens.sign(
                subject=client.client_id,
                audience=audience,
                use=TokenUse.APP,
                lifetime=ACCESS_LIFETIME,
                claims={'org': org_id, 'kind': APP, 'name': client.name},
            )
        return IssuedTokens(
            access_token=access, expires_in=int(ACCESS_LIFETIME.total_seconds()), issued_token_type=ACCESS_TOKEN_TYPE
        )

    async def _as_space(self, request: TokenRequest) -> IssuedTokens:
        """An app's token traded for one of its spaces': the app acting as that end user's producer."""
        payload = self._tokens.verify(request.subject_token or '', audience=self._tokens.issuer, use=TokenUse.APP)
        if payload is None:
            raise OAuthError.invalid_grant("subject_token must be the app's own token")
        org_id = str(payload.get('org') or '')
        space = await SpaceTokens.live(org_id, request.space or '', app_id=str(payload['sub']))
        if space is None:
            raise OAuthError.invalid_grant('no such space of this app')
        logger.info('App %s acts as its space %s', space.app_id, space.id)
        return IssuedTokens(
            access_token=SpaceTokens(self._tokens).sign(org_id, space, lifetime=ACCESS_LIFETIME),
            expires_in=int(ACCESS_LIFETIME.total_seconds()),
            issued_token_type=ACCESS_TOKEN_TYPE,
        )

    def _access(self, audience: str, person: PersonInOrganisation, *, claims: dict[str, object]) -> str:
        return self._tokens.sign(
            subject=person.user_id,
            audience=audience,
            use=TokenUse.USER,
            lifetime=ACCESS_LIFETIME,
            claims={**person.claims(), **claims},
        )

    def _for_person(self, client: RegisteredClient, person: PersonInOrganisation, *, grant_id: str) -> IssuedTokens:
        """An access token for the client's URL, and a refresh token bound to the client.

        The refresh token names its sign-in (`grant`), which is what a
        revocation stops, and has an id of its own (`jti`).
        """
        refresh = self._tokens.sign(
            subject=person.user_id,
            audience=self._tokens.issuer,
            use=TokenUse.REFRESH,
            lifetime=REFRESH_LIFETIME,
            claims={'org': person.org_id, 'azp': client.client_id, 'grant': grant_id, 'jti': uuid.uuid4().hex},
        )
        return IssuedTokens(
            access_token=self._access(client.base_url, person, claims={'azp': client.client_id}),
            expires_in=int(ACCESS_LIFETIME.total_seconds()),
            refresh_token=refresh,
        )
