"""Who is calling, when a platform client calls igni.

The cloud's gateway and node agent are **platform clients**: registered
like a service (`POST /v1/clients` with `kind: platform`), approved once
by a super admin on the Services page, and acting for the whole
deployment rather than one organisation. They present a `platform` token
obtained with `client_credentials` (`docs/service-identity.md` §11).
The client is looked up on every call, so revoking it stops it at once.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

from app.db.sql.models.registered_client import PLATFORM
from app.middlewares.audit import set_audit_actor
from app.services.auth.jwt_service import JWTService, TokenUse
from app.services.identity.registry import ClientRegistry

_bearer = OAuth2PasswordBearer(tokenUrl='/v1/oauth/token', auto_error=False)


class PlatformCaller(BaseModel):
    """An approved platform client."""

    client_id: str
    name: str


async def require_platform(request: Request, token: str | None = Depends(_bearer)) -> PlatformCaller:
    tokens = JWTService()
    payload = tokens.verify(token or '', audience=tokens.issuer, use=TokenUse.PLATFORM)
    client = await ClientRegistry().approved(str(payload['sub']), kind=PLATFORM) if payload else None
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='A platform token from an approved platform client is required',
            headers={'WWW-Authenticate': 'Bearer'},
        )
    set_audit_actor(request, user_id=f'client:{client.client_id}')
    return PlatformCaller(client_id=client.client_id, name=client.name)
