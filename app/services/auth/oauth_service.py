"""Social sign-in. Google is the one provider.

GitHub and GitLab are not ways into this deployment: they belong to
CodeIndex, which connects repositories. Company sign-in is email or the
deployment's own identity provider (`oidc.py`).
"""

from enum import StrEnum
import logging
import secrets

import bcrypt
from fastapi import HTTPException, status
import httpx
from pydantic import BaseModel

from app.config import settings
from app.db.redis.oauth_state import OAuthStateDB
from app.db.sql.user import UserDB
from app.security.subject import refuse_if_deactivated
from app.services.auth.jwt_service import JWTService, TokenResponse


class OAuthTokenResult(BaseModel):
    """Token + metadata so the caller knows whether to send a welcome email."""

    token: TokenResponse
    is_new_user: bool
    user_id: str
    email: str
    full_name: str | None = None


logger = logging.getLogger(__name__)

# Every outbound call in a sign-in, on one budget.
#
# These were synchronous `httpx.post` / `httpx.get` inside `async def`,
# which blocks the event loop rather than yielding it — and the API runs
# a single uvicorn worker, so one sign-in stopped the whole process.
# Measured against a deliberately slow token endpoint: with the upstream
# taking 3 seconds, `/health` took 3.1 seconds to answer. F118.
#
# `connect` is separate and short because a provider that is unreachable
# should fail fast, while one that is merely slow to answer gets the
# longer read budget. The total per sign-in is now bounded by this rather
# than by the old 15+10+10 spread across three independent calls.
_EXCHANGE_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

#: Google's OAuth endpoints. Named rather than inlined so a test can point
#: a sign-in at a slow local server and measure what it does to the loop.
GOOGLE_AUTHORIZE_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
GOOGLE_USERINFO_URL = 'https://www.googleapis.com/oauth2/v2/userinfo'

_user_db = UserDB()
_oauth_state_db = OAuthStateDB()


class OAuthProvider(StrEnum):
    """Supported OAuth providers."""

    GOOGLE = 'google'


class OAuthUserInfo(BaseModel):
    email: str
    first_name: str
    last_name: str
    provider: str = 'email'


class OAuthService:
    """Handles OAuth authorization URL generation, code exchange, and user info retrieval."""

    @staticmethod
    async def generate_state(metadata: dict[str, str] | None = None) -> str:
        """Generate a CSRF state token and store it with optional metadata."""
        state = secrets.token_urlsafe(32)
        await _oauth_state_db.store(state, metadata)
        return state

    @staticmethod
    async def validate_state(state: str) -> dict[str, str]:
        """Validate and consume a CSRF state token. Returns stored metadata."""
        result = await _oauth_state_db.validate_and_consume(state)
        if result is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail='Invalid OAuth state')
        return result

    @staticmethod
    async def get_authorize_url(provider: OAuthProvider, state: str) -> str:
        """Return the authorization URL for the given provider."""
        match provider:
            case OAuthProvider.GOOGLE:
                return OAuthService._google_authorize_url(state)

    @staticmethod
    async def exchange_and_get_user(provider: OAuthProvider, code: str) -> OAuthUserInfo:
        """Exchange the authorization code for user info."""
        match provider:
            case OAuthProvider.GOOGLE:
                return await OAuthService._google_exchange_and_get_user(code)

    @staticmethod
    def _google_authorize_url(state: str) -> str:
        redirect_uri = f'{settings.oauth_redirect_base_url}/v1/auth/google/callback'
        return (
            f'{GOOGLE_AUTHORIZE_URL}?client_id={settings.oauth_google_client_id}'
            f'&redirect_uri={redirect_uri}'
            f'&response_type=code'
            f'&scope=openid+email+profile'
            f'&state={state}'
            f'&access_type=offline'
            f'&prompt=consent'
        )

    @staticmethod
    async def _google_exchange_and_get_user(code: str) -> OAuthUserInfo:
        redirect_uri = f'{settings.oauth_redirect_base_url}/v1/auth/google/callback'

        async with httpx.AsyncClient(timeout=_EXCHANGE_TIMEOUT) as client:
            resp = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    'client_id': settings.oauth_google_client_id,
                    'client_secret': settings.oauth_google_client_secret,
                    'code': code,
                    'grant_type': 'authorization_code',
                    'redirect_uri': redirect_uri,
                },
            )
            resp.raise_for_status()
            access_token = resp.json().get('access_token')
            if not access_token:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail='Google OAuth failed')

            user_resp = await client.get(
                GOOGLE_USERINFO_URL,
                headers={'Authorization': f'Bearer {access_token}'},
            )
            user_resp.raise_for_status()
            info = user_resp.json()

        email = info.get('email')
        if not email:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail='Could not retrieve email from Google')

        return OAuthUserInfo(
            email=email,
            first_name=info.get('given_name', ''),
            last_name=info.get('family_name', ''),
            provider='google',
        )

    # ── Common: find-or-create user + issue token ──────────────────────

    @staticmethod
    async def find_or_create_user_and_get_token(user_info: OAuthUserInfo) -> OAuthTokenResult:
        """Find the account by address, or create it; then issue a JWT."""
        full_name = f'{user_info.first_name} {user_info.last_name}'.strip() or None
        is_new = False

        user = await _user_db.get_by_email(user_info.email)

        refuse_if_deactivated(user)

        if not user:
            random_pw = secrets.token_urlsafe(16)
            hashed = bcrypt.hashpw(random_pw.encode(), bcrypt.gensalt()).decode()
            user = await _user_db.create(
                email=user_info.email,
                hashed_password=hashed,
                full_name=full_name,
                auth_provider=user_info.provider,
            )
            is_new = True
            logger.info(f'Created new OAuth user: {user_info.email}')

            # Already in the organisation that owns their address's
            # domain, with any pending invitation claimed: `UserDB.create`
            # did both, in the same transaction as the account.
        else:
            logger.debug(f'OAuth user already exists: {user.email}')

        token = JWTService().create_token(str(user.user_id))
        await _user_db.note_sign_in(str(user.user_id))
        return OAuthTokenResult(
            token=TokenResponse(access_token=token),
            is_new_user=is_new,
            user_id=str(user.user_id),
            email=user_info.email,
            full_name=full_name,
        )
