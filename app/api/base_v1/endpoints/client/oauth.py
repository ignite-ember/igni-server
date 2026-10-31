"""OAuth endpoints for social sign-in (Google)."""

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from fastapi.responses import RedirectResponse

from app.config import settings
from app.db.redis.auth_handoff import AuthHandoffDB
from app.services.auth.oauth_service import OAuthProvider, OAuthService
from app.services.auth.providers import is_usable
from app.services.email.delivery import EmailDelivery
from app.services.email.service import EmailService

logger = logging.getLogger(__name__)

router = APIRouter()

_handoff = AuthHandoffDB()


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


@router.get(
    '/auth/{provider}',
    description='Redirect to OAuth provider authorization page',
)
async def oauth_redirect(
    provider: str,
    returnTo: str = Query(default='', alias='returnTo'),
):
    resolved = await _resolve_provider(provider)

    metadata = {'returnTo': returnTo} if returnTo else None
    state = await OAuthService.generate_state(metadata)
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
):
    resolved = await _resolve_provider(provider)
    metadata = await OAuthService.validate_state(state)

    try:
        user_info = await OAuthService.exchange_and_get_user(resolved, code)
        result = await OAuthService.find_or_create_user_and_get_token(user_info)

        if result.is_new_user:
            if EmailDelivery().enabled:
                bg.add_task(EmailService().send_welcome, to_email=result.email)
            else:
                EmailDelivery().note_undeliverable(purpose='welcome', email=result.email)

        # A one-time code, never the token. `?token=<jwt>` put a working
        # credential in the browser history, in the `Referer` of
        # anything this page touches, and in the access log of whatever
        # proxies the portal. See app/db/redis/auth_handoff.py.
        query: dict[str, str] = {'code': await _handoff.issue(result.token.access_token)}
        return_to = metadata.get('returnTo', '')
        if return_to:
            query['returnTo'] = return_to
        params = urlencode(query)
        return RedirectResponse(
            url=f'{settings.oauth_frontend_base_url}/login?{params}',
            status_code=status.HTTP_302_FOUND,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'OAuth callback error for {provider}: {e}', exc_info=True)
        params = urlencode({'error': f'OAuth sign-in failed: {e}'})
        return RedirectResponse(
            url=f'{settings.oauth_frontend_base_url}/login?{params}',
            status_code=status.HTTP_302_FOUND,
        )
