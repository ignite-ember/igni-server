"""Google's half of a social sign-in: where to send the person, and what their code is worth."""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from app.config import settings
from app.services.auth.oauth_errors import ProviderAnswerRefused
from app.services.auth.oauth_models import OAuthUserInfo

GOOGLE_AUTHORIZE_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
GOOGLE_USERINFO_URL = 'https://www.googleapis.com/oauth2/v2/userinfo'

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
EXCHANGE_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class GoogleToken(BaseModel):
    """Google's token endpoint answer: only the access token is read."""

    access_token: str = ''


class GoogleProfile(BaseModel):
    """Google's userinfo answer, as far as a sign-in needs it."""

    email: str = ''
    given_name: str = ''
    family_name: str = ''


class GoogleSignIn:
    """Builds Google's authorize URL and exchanges a code for the person it names.

    The endpoints are constructor arguments so a test can point a
    sign-in at a slow local server and measure what it does to the loop.
    """

    def __init__(
        self,
        *,
        authorize_url: str = GOOGLE_AUTHORIZE_URL,
        token_url: str = GOOGLE_TOKEN_URL,
        userinfo_url: str = GOOGLE_USERINFO_URL,
        timeout: httpx.Timeout = EXCHANGE_TIMEOUT,
    ) -> None:
        self._authorize_url = authorize_url
        self._token_url = token_url
        self._userinfo_url = userinfo_url
        self._timeout = timeout

    @staticmethod
    def _redirect_uri() -> str:
        return f'{settings.oauth_redirect_base_url}/v1/auth/google/callback'

    def authorize_url(self, state: str) -> str:
        """Where to send the person, with every parameter URL-encoded."""
        query = urlencode(
            {
                'client_id': settings.oauth_google_client_id,
                'redirect_uri': self._redirect_uri(),
                'response_type': 'code',
                'scope': 'openid email profile',
                'state': state,
                'access_type': 'offline',
                'prompt': 'consent',
            }
        )
        return f'{self._authorize_url}?{query}'

    async def exchange(self, code: str) -> OAuthUserInfo:
        """Exchange `code` for the person Google says it belongs to.

        Raises `httpx.HTTPError` for transport and status failures,
        `ValueError` for a body that is not the JSON expected, and
        `ProviderAnswerRefused` for an answer without a token or address.
        """
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                self._token_url,
                data={
                    'client_id': settings.oauth_google_client_id,
                    'client_secret': settings.oauth_google_client_secret,
                    'code': code,
                    'grant_type': 'authorization_code',
                    'redirect_uri': self._redirect_uri(),
                },
            )
            resp.raise_for_status()
            token = GoogleToken.model_validate_json(resp.content)
            if not token.access_token:
                raise ProviderAnswerRefused('Google OAuth failed')

            user_resp = await client.get(
                self._userinfo_url,
                headers={'Authorization': f'Bearer {token.access_token}'},
            )
            user_resp.raise_for_status()
            profile = GoogleProfile.model_validate_json(user_resp.content)

        if not profile.email:
            raise ProviderAnswerRefused('Could not retrieve email from Google')

        return OAuthUserInfo(
            email=profile.email,
            first_name=profile.given_name,
            last_name=profile.family_name,
            provider='google',
        )
