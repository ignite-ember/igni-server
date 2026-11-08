"""OIDC sign-in against the customer's identity provider.

Kept separate from the social-login endpoints rather than added as a
fourth provider there, because it differs in three ways that would have
made that file worse: it is configured rather than hardcoded, the round
trip carries a nonce, and it is the only provider an air-gapped
deployment can actually reach.

See ``app/services/auth/oidc.py`` for what is validated on the way back.
"""

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from app.api.base_v1.schema.oidc import OidcSignInState
from app.config import settings
from app.db.redis.auth_handoff import AuthHandoffDB
from app.services.auth.auth_refusals import AuthRefusals
from app.services.auth.oauth_models import OAuthUserInfo
from app.services.auth.oauth_refusals import OAuthRefusals
from app.services.auth.oauth_service import oauth_service, OAuthService
from app.services.auth.oidc import OidcClient, OidcError, OidcNotConfigured
from app.services.auth.person_name import email_local_part, split_full_name
from app.services.identity.account_refusals import AccountRefusals

logger = logging.getLogger(__name__)

router = APIRouter()

_handoff = AuthHandoffDB()


@router.get('/auth/oidc', description="Redirect to the deployment's identity provider")
async def oidc_redirect(
    returnTo: str = Query(default='', alias='returnTo'),
    oauth: OAuthService = Depends(oauth_service),
):
    client = OidcClient()
    if not client.is_configured:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail='OIDC sign-in is not configured on this deployment',
        )

    nonce = client.generate_nonce()
    verifier = client.generate_pkce_verifier()
    # The nonce and the PKCE verifier ride in the same Redis-backed state
    # record as the CSRF token, so both are single-use and expire with
    # it. The verifier must never reach the browser — only its S256
    # challenge does — which is the reason it lives server-side rather
    # than in a cookie.
    state = await oauth.generate_state(OidcSignInState(nonce=nonce, pkce=verifier, returnTo=returnTo))

    try:
        url = await client.authorize_url(state=state, nonce=nonce, code_challenge=client.pkce_challenge(verifier))
    except (OidcError, OidcNotConfigured) as exc:
        logger.warning('OIDC authorize failed: %s', exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail='Could not reach the identity provider',
        ) from exc
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@router.get('/auth/oidc/callback', description='OIDC callback — validates the token and signs the user in')
async def oidc_callback(
    code: str = Query(...),
    state: str = Query(...),
    oauth: OAuthService = Depends(oauth_service),
):
    client = OidcClient()
    if not client.is_configured:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail='OIDC sign-in is not configured')

    try:
        # State missing either secret did not come from our redirect;
        # `OidcSignInState` requires both, and records why on each field.
        with OAuthRefusals():
            signin = await oauth.validate_state(state, OidcSignInState)
    except ValidationError as exc:
        logger.warning('OIDC callback state is not a sign-in state: %s', exc.error_count())
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail='Invalid OIDC state') from exc

    try:
        claims = await client.exchange(code=code, nonce=signin.nonce, code_verifier=signin.pkce)
    except OidcError as exc:
        # The specific reason goes to the log and not to the caller: a
        # bad signature and a replayed nonce are equally useful to an
        # operator and to an attacker.
        logger.warning('OIDC sign-in rejected: %s', exc)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail='Sign-in failed') from exc

    if not claims.email_verified and not settings.oidc_link_by_email:
        # Without a verified address we cannot safely match an existing
        # account, and creating a second one on an unverified address is
        # how duplicate identities accumulate.
        logger.warning('OIDC sign-in for %s carried no verified email', claims.subject)
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail='Your identity provider did not supply a verified email address',
        )

    first, last = split_full_name(claims.full_name)
    with AuthRefusals(), AccountRefusals():
        result = await oauth.find_or_create_user_and_get_token(
            OAuthUserInfo(
                email=claims.email,
                first_name=first or email_local_part(claims.email),
                last_name=last,
                provider='oidc',
            )
        )

    # A one-time code, never the token — the same reasoning as the other
    # providers. See app/db/redis/auth_handoff.py.
    #
    # To the sign-in page, as the other providers do: that is where the
    # portal exchanges the code. This said `/auth/callback`, a path the
    # portal has no route for, so its catch-all sent the browser to the
    # dashboard with the code dropped, and from there back to sign in.
    # Every SSO login succeeded at the provider and here, and signed
    # nobody in. Found by the journey (tools/e2e_journey) against Dex.
    query: dict[str, str] = {'code': await _handoff.issue(result.token.access_token)}
    if signin.return_to:
        query['returnTo'] = signin.return_to
    return RedirectResponse(
        url=f'{settings.oauth_frontend_base_url.rstrip("/")}/login?{urlencode(query)}',
        status_code=status.HTTP_302_FOUND,
    )
