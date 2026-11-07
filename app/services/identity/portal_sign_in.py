"""A person signing in to a registered client through the portal.

The rules of the authorization endpoint: the client must be approved,
the redirect URI one it registered, the challenge S256, and the
organisation one the person belongs to. Only then is a one-time code
issued, bound to all of it. Each refusal is a typed error; the router
decides what HTTP makes of it (`app.services.identity.sign_in_refusals`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode

from app.api.base_v1.schema.portal.authorize import AuthorizationAsk, Authorize, OrganisationChoice, Redirect
from app.api.base_v1.schema.portal.user import PortalAccount
from app.db.redis.authorization_codes import AuthorizationCodeDB, PendingAuthorization
from app.db.sql.models.registered_client import RegisteredClient
from app.db.sql.organizations import OrganizationDB
from app.services.identity.registry import ClientRegistry

#: The only PKCE method accepted; `plain` would hand the verifier to anyone who saw the request.
S256 = 'S256'


class SignInRefused(Exception):
    """The sign-in cannot go ahead; the message says why, in words for the person."""


class ClientNotApproved(SignInRefused):
    def __init__(self) -> None:
        super().__init__('This application is not approved here')


class RedirectNotRegistered(SignInRefused):
    def __init__(self) -> None:
        super().__init__('This redirect address was not registered by the application')


class ChallengeMethodRefused(SignInRefused):
    def __init__(self) -> None:
        super().__init__('Only S256 code challenges are accepted')


class NotAnOrganisationMember(SignInRefused):
    def __init__(self) -> None:
        super().__init__('Not a member of this organisation')


class PortalSignIn:
    """Describes a sign-in request to the person, and issues its code once they choose."""

    def __init__(
        self,
        *,
        registry: ClientRegistry | None = None,
        organisations: OrganizationDB | None = None,
        codes: AuthorizationCodeDB | None = None,
    ) -> None:
        self._registry = registry or ClientRegistry()
        self._organisations = organisations or OrganizationDB()
        self._codes = codes or AuthorizationCodeDB()

    async def describe(self, account: PortalAccount, *, client_id: str, redirect_uri: str) -> AuthorizationAsk:
        client = await self._client(client_id, redirect_uri)
        return AuthorizationAsk(client_name=client.name, organisations=await self._choices(account.user_id))

    async def authorize(self, account: PortalAccount, request: Authorize) -> Redirect:
        client = await self._client(request.client_id, request.redirect_uri)
        if request.code_challenge_method != S256:
            raise ChallengeMethodRefused()
        if request.organisation not in {choice.org_id for choice in await self._choices(account.user_id)}:
            raise NotAnOrganisationMember()
        code = await self._codes.issue(
            PendingAuthorization(
                client_id=client.client_id,
                redirect_uri=request.redirect_uri,
                code_challenge=request.code_challenge,
                user_id=account.user_id,
                org_id=request.organisation,
                session_issued_at=account.session_issued_at or int(datetime.now(timezone.utc).timestamp()),
            )
        )
        return Redirect(redirect_to=self._redirect(request, code))

    async def _client(self, client_id: str, redirect_uri: str) -> RegisteredClient:
        client = await self._registry.approved(client_id)
        if client is None:
            raise ClientNotApproved()
        if redirect_uri not in (client.redirect_uris or []):
            raise RedirectNotRegistered()
        return client

    async def _choices(self, user_id: str) -> list[OrganisationChoice]:
        memberships = await self._organisations.list_user_orgs(user_id)
        return [OrganisationChoice(org_id=str(org.id), name=str(org.display_name)) for org, _membership in memberships]

    @staticmethod
    def _redirect(request: Authorize, code: str) -> str:
        query = {'code': code, **({'state': request.state} if request.state else {})}
        separator = '&' if '?' in request.redirect_uri else '?'
        return f'{request.redirect_uri}{separator}{urlencode(query)}'
