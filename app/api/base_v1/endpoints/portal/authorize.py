"""A person signing in to a registered client through the portal.

The authorization endpoint in igni's metadata is the portal's
`/authorize` page, because that is where the person's session is. The
page asks `GET /oauth/request` what is being asked for — refused here,
and never redirected, if the client is not approved or the redirect URI
is not one it registered — lets the person pick an organisation, and
posts `POST /oauth/authorize` for a one-time code to send back.

No consent screen beyond the organisation choice: a client is approved
by a super admin for the whole install, so a person is not being asked
to trust a stranger. The rules are `PortalSignIn`'s.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.base_v1.schema.portal.authorize import AuthorizationAsk, Authorize, Redirect
from app.api.base_v1.schema.portal.user import PortalAccount
from app.security.api_key_sessions import no_api_key_session
from app.security.portal_auth import get_current_account
from app.services.identity.portal_sign_in import PortalSignIn
from app.services.identity.sign_in_refusals import SignInRefusals

router = APIRouter()


@router.get('/oauth/request', response_model=AuthorizationAsk)
async def describe(
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    account: PortalAccount = Depends(get_current_account),
) -> AuthorizationAsk:
    with SignInRefusals():
        return await PortalSignIn().describe(account, client_id=client_id, redirect_uri=redirect_uri)


@router.post('/oauth/authorize', response_model=Redirect, dependencies=[Depends(no_api_key_session)])
async def authorize(body: Authorize, account: PortalAccount = Depends(get_current_account)) -> Redirect:
    with SignInRefusals():
        return await PortalSignIn().authorize(account, body)
