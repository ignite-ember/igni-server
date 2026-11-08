"""What a social or OIDC sign-in carries between the provider, the service and the router."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from app.services.auth.jwt_service import TokenResponse


class OAuthProvider(StrEnum):
    """Supported OAuth providers."""

    GOOGLE = 'google'


class OAuthUserInfo(BaseModel):
    """The person a provider vouched for."""

    email: str
    first_name: str
    last_name: str
    provider: str = 'email'


class OAuthTokenResult(BaseModel):
    """Token + metadata so the caller knows whether to send a welcome email."""

    token: TokenResponse
    is_new_user: bool
    user_id: str
    email: str
    full_name: str | None = None
