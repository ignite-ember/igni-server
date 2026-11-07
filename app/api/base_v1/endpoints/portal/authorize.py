"""A person signing in to a registered client through the portal.

The authorization endpoint in igni's metadata is the portal's
`/authorize` page, because that is where the person's session is. The
page asks `GET /oauth/request` what is being asked for — refused here,
and never redirected, if the client is not approved or the redirect URI
is not one it registered — lets the person pick an organisation, and
posts `POST /oauth/authorize` for a one-time code to send back.

No consent screen beyond the organisation choice: a client is approved
by a super admin for the whole install, so a person is not being asked
to trust a stranger.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.base_v1.schema.portal.user import PortalAccount
from app.db.redis.authorization_codes import AuthorizationCodeDB, PendingAuthorization
from app.db.sql.models.registered_client import RegisteredClient
from app.db.sql.organizations import OrganizationDB
from app.security.api_key_sessions import no_api_key_session
from app.security.portal_auth import get_current_account
from app.services.identity.registry import ClientRegistry

router = APIRouter()


class OrganisationChoice(BaseModel):
    org_id: str
    name: str


class AuthorizationAsk(BaseModel):
    client_name: str
    organisations: list[OrganisationChoice]


class Authorize(BaseModel):
    client_id: str
    redirect_uri: str
    organisation: str
    code_challenge: str = Field(min_length=43, max_length=128)
    code_challenge_method: str = 'S256'
    state: str = Field(default='', max_length=1000)


class Redirect(BaseModel):
    redirect_to: str


async def _client(client_id: str, redirect_uri: str) -> RegisteredClient:
    client = await ClientRegistry().approved(client_id)
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='This application is not approved here')
    if redirect_uri not in (client.redirect_uris or []):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='This redirect address was not registered by the application',
        )
    return client


@router.get('/oauth/request', response_model=AuthorizationAsk)
async def describe(
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    account: PortalAccount = Depends(get_current_account),
) -> AuthorizationAsk:
    client = await _client(client_id, redirect_uri)
    memberships = await OrganizationDB().list_user_orgs(account.user_id)
    return AuthorizationAsk(
        client_name=client.name,
        organisations=[
            OrganisationChoice(org_id=str(org.id), name=str(org.display_name)) for org, _membership in memberships
        ],
    )


@router.post('/oauth/authorize', response_model=Redirect, dependencies=[Depends(no_api_key_session)])
async def authorize(body: Authorize, account: PortalAccount = Depends(get_current_account)) -> Redirect:
    client = await _client(body.client_id, body.redirect_uri)
    if body.code_challenge_method != 'S256':
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Only S256 code challenges are accepted')
    memberships = await OrganizationDB().list_user_orgs(account.user_id)
    if body.organisation not in {str(org.id) for org, _membership in memberships}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Not a member of this organisation')
    code = await AuthorizationCodeDB().issue(
        PendingAuthorization(
            client_id=client.client_id,
            redirect_uri=body.redirect_uri,
            code_challenge=body.code_challenge,
            user_id=account.user_id,
            org_id=body.organisation,
            session_issued_at=account.session_issued_at or int(datetime.now(timezone.utc).timestamp()),
        )
    )
    query = {'code': code, **({'state': body.state} if body.state else {})}
    separator = '&' if '?' in body.redirect_uri else '?'
    return Redirect(redirect_to=f'{body.redirect_uri}{separator}{urlencode(query)}')
