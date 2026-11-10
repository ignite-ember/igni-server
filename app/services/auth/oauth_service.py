"""Social sign-in. Google is the one provider.

GitHub and GitLab are not ways into this deployment: they belong to
CodeIndex, which connects repositories. Company sign-in is email or the
deployment's own identity provider (`oidc.py`).

Each refusal is a typed error and this module decides no status code:
the router turns them into HTTP through `OAuthRefusals`, `AuthRefusals`
and `AccountRefusals`.
"""

from __future__ import annotations

import logging
import secrets

import httpx
from pydantic import BaseModel

from app.db.redis.oauth_state import OAuthStateDB
from app.db.sql.user import UserDB
from app.security.subject import Subjects
from app.services.auth.google_sign_in import GoogleSignIn
from app.services.auth.jwt_service import JWTService, TokenResponse
from app.services.auth.oauth_errors import InvalidOAuthState, OAuthExchangeFailed
from app.services.auth.oauth_models import OAuthProvider, OAuthTokenResult, OAuthUserInfo
from app.services.auth.passwords import Passwords

logger = logging.getLogger(__name__)


class OAuthService:
    """CSRF state, the provider round trip, and the account and token at the end of it."""

    def __init__(
        self,
        *,
        users: UserDB | None = None,
        states: OAuthStateDB | None = None,
        passwords: Passwords | None = None,
        jwt: JWTService | None = None,
        google: GoogleSignIn | None = None,
    ) -> None:
        self._users = users or UserDB()
        self._states = states or OAuthStateDB()
        self._passwords = passwords or Passwords()
        self._jwt = jwt or JWTService()
        self._google = google or GoogleSignIn()

    async def generate_state(self, record: BaseModel) -> str:
        """Generate a CSRF state token and store `record` with it."""
        state = secrets.token_urlsafe(32)
        await self._states.store(state, record)
        return state

    async def validate_state[R: BaseModel](self, state: str, shape: type[R]) -> R:
        """Consume a CSRF state token and return its record as `shape`.

        A token this server did not issue, or has already consumed, raises
        `InvalidOAuthState`. A record that is not a `shape` raises
        `ValidationError`.
        """
        record = await self._states.claim(state, shape)
        if record is None:
            raise InvalidOAuthState()
        return record

    def get_authorize_url(self, provider: OAuthProvider, state: str) -> str:
        """Return the authorization URL for the given provider."""
        match provider:
            case OAuthProvider.GOOGLE:
                return self._google.authorize_url(state)

    async def exchange_and_get_user(self, provider: OAuthProvider, code: str) -> OAuthUserInfo:
        """Exchange the authorization code for user info.

        Raises `OAuthExchangeFailed` for a transport failure, an error
        status, or a body that is not the JSON expected (`ValueError`
        covers a JSON decode error and a pydantic validation error).
        A `ProviderAnswerRefused` — a provider answer this server
        refuses — passes through unchanged.
        """
        try:
            match provider:
                case OAuthProvider.GOOGLE:
                    return await self._google.exchange(code)
        except (httpx.HTTPError, ValueError) as exc:
            raise OAuthExchangeFailed(provider) from exc

    async def find_or_create_user_and_get_token(self, user_info: OAuthUserInfo) -> OAuthTokenResult:
        """Find the account by address, or create it; then issue a JWT.

        A new account is already in the organisation that owns its
        address's domain, with any pending invitation claimed:
        `UserDB.create` does both, in the same transaction as the account.
        """
        full_name = f'{user_info.first_name} {user_info.last_name}'.strip() or None

        user = await self._users.get_by_email(user_info.email)
        Subjects.refuse_if_deactivated(user)

        is_new = user is None
        if user is None:
            user = await self._users.create(
                user_info.email,
                hashed_password=self._passwords.hash(secrets.token_urlsafe(16)),
                full_name=full_name,
                auth_provider=user_info.provider,
            )
            logger.info('Created new OAuth user %s', user.user_id)
        else:
            logger.debug('OAuth user %s already exists', user.user_id)

        user_id = str(user.user_id)
        token = self._jwt.create_token(user_id)
        await self._users.note_sign_in(user_id)
        return OAuthTokenResult(
            token=TokenResponse(access_token=token),
            is_new_user=is_new,
            user_id=user_id,
            email=user_info.email,
            full_name=full_name,
        )


def oauth_service() -> OAuthService:
    """The FastAPI dependency; tests replace it through `app.dependency_overrides`."""
    return OAuthService()
