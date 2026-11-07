"""OAuth endpoints for social sign-in (Google)."""

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse

from app.api.base_v1.schema.oauth import SocialSignInState
from app.config import settings
from app.db.redis.auth_handoff import auth_handoff, AuthHandoffDB
from app.db.sql.schemas.org_domain import AdmissionRefused
from app.services.auth.auth_emails import auth_emails, AuthEmails
from app.services.auth.oauth_service import OAuthExchangeFailed, OAuthProvider, OAuthService
from app.services.auth.providers import is_usable

logger = logging.getLogger(__name__)

router = APIRouter()


async def _resolve_provider(name: str) -> OAuthProvider:
    """Validate and resolve a provider path parameter to the enum.

    Enabled is not the same as usable, and this used to check only the
    first. A provider the operator listed but never gave credentials
    to redirected to the provider with ``client_id=`` — so the error
    the person saw came from the provider, about a request this server
    built. Refused here instead, where the reason is known.
    """
    try:
        provider = OAuthProvider(name)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f'Unsupported provider: {name}')

    if provider.value not in settings.oauth_providers:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f'Provider {name} is not enabled')

    if not await is_usable(provider.value):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f'Provider {name} is enabled but has no credentials configured',
        )

    return provider


def _to_login(query: dict[str, str]) -> RedirectResponse:
    return RedirectResponse(
        url=f'{settings.oauth_frontend_base_url}/login?{urlencode(query)}',
        status_code=status.HTTP_302_FOUND,
    )


@router.get(
    '/auth/{provider}',
    description='Redirect to OAuth provider authorization page',
)
async def oauth_redirect(
    provider: str,
    returnTo: str = Query(default='', alias='returnTo'),
):
    resolved = await _resolve_provider(provider)

    state = await OAuthService.generate_state(SocialSignInState(returnTo=returnTo))
    url = await OAuthService.get_authorize_url(resolved, state)
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@router.get(
    '/auth/{provider}/callback',
    description='OAuth callback — exchanges code for token and redirects to frontend',
)
async def oauth_callback(
    provider: str,
    bg: BackgroundTasks,
    code: str = Query(...),
    state: str = Query(...),
    emails: AuthEmails = Depends(auth_emails),
    handoff: AuthHandoffDB = Depends(auth_handoff),
):
    resolved = await _resolve_provider(provider)
    signin = await OAuthService.validate_state(state, SocialSignInState)

    try:
        user_info = await OAuthService.exchange_and_get_user(resolved, code)
    except OAuthExchangeFailed as exc:
        # The cause goes to the log, not into the URL: it was `str(e)`
        # in the redirect, which showed the person an upstream error
        # body and left it in their history and the proxy's access log.
        logger.warning('OAuth exchange with %s failed', resolved.value, exc_info=exc)
        return _to_login({'error': 'OAuth sign-in failed. Please try again.'})

    try:
        result = await OAuthService.find_or_create_user_and_get_token(user_info)
    except AdmissionRefused as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if result.is_new_user:
        emails.welcome(bg, email=result.email)

    # A one-time code, never the token. `?token=<jwt>` put a working
    # credential in the browser history, in the `Referer` of
    # anything this page touches, and in the access log of whatever
    # proxies the portal. See app/db/redis/auth_handoff.py.
    query: dict[str, str] = {'code': await handoff.issue(result.token.access_token)}
    if signin.return_to:
        query['returnTo'] = signin.return_to
    return _to_login(query)
