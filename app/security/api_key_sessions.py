"""A session bought with a personal API key may do what its person may — except mint more credentials.

A key is handed to a program, so it is the credential most likely to
leak. What it buys is a 15-minute session (`docs/service-identity.md`
§22), and nothing on it turns into longer-lived access: it cannot make,
list or revoke API keys, take a 30-day CLI session (`/v1/cli/token`),
sign a cloud VM in (`vm-sign-in`), or authorise a service to a refresh
token (`/v1/portal/oauth/authorize`). A leaked key is then worth exactly
what it was until it is revoked or expires, and no more.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.services.auth.jwt_service import JWTService

_optional_oauth2 = OAuth2PasswordBearer(tokenUrl='token', auto_error=False)

#: What a session from an API key is told when it reaches for more.
REFUSED = 'A session from an API key cannot make further credentials. Sign in to do this.'


def is_api_key_session(token: str | None) -> bool:
    """Whether `token` is a session an API key bought."""
    return bool(token) and JWTService().decode_token(token or '').api_key_id is not None


async def no_api_key_session(authorization: str | None = Depends(_optional_oauth2)) -> None:
    """A route dependency: 403 for a session an API key bought."""
    if is_api_key_session(authorization):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=REFUSED)
