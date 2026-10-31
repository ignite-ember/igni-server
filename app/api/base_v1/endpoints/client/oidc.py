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

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from app.api.base_v1.schema.oidc import OidcSignInState
from app.config import settings
from app.db.redis.auth_handoff import AuthHandoffDB
from app.services.auth.oauth_service import OAuthService, OAuthUserInfo
from app.services.auth.oidc import OidcClient, OidcError, OidcNotConfigured
from app.services.auth.person_name import email_local_part, split_full_name

logger = logging.getLogger(__name__)

router = APIRouter()

_handoff = AuthHandoffDB()


@router.get('/auth/oidc', description="Redirect to the deployment's identity provider")
async def oidc_redirect(returnTo: str = Query(default='', alias='returnTo')):
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
    state = await OAuthService.generate_state(
        OidcSignInState(nonce=nonce, pkce=verifier, returnTo=returnTo).as_metadata()
    )

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
async def oidc_callback(code: str = Query(...), state: str = Query(...)):
    client = OidcClient()
    if not client.is_configured:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail='OIDC sign-in is not configured')

    metadata = await OAuthService.validate_state(state)
    try:
        # State missing either secret did not come from our redirect.
        # Both were checked here by hand; `OidcSignInState` requires
        # them, and the reason each one is mandatory is recorded on the
        # field rather than in a comment beside one reader.
        #
        # The shared state store is a flat `dict[str, str]` because four
        # callers put different things in it. What *this* pair of
        # handlers agrees on is this model.
        signin = OidcSignInState.model_validate(metadata)
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
    result = await OAuthService.find_or_create_user_and_get_token(
        OAuthUserInfo(
            email=claims.email,
            first_name=first or email_local_part(claims.email),
            last_name=last,
            provider='oidc',
        )
    )

    # A one-time code, never the token — the same reasoning as the other
    # providers. See app/db/redis/auth_handoff.py.
    query: dict[str, str] = {'code': await _handoff.issue(result.token.access_token)}
    if signin.return_to:
        query['returnTo'] = signin.return_to
    return RedirectResponse(
        url=f'{settings.oauth_frontend_base_url.rstrip("/")}/auth/callback?{urlencode(query)}',
        status_code=status.HTTP_302_FOUND,
    )
