"""Who is calling, when a registered service calls igni.

A service presents a token it obtained with `client_credentials`: for
igni (`aud` = the issuer), of kind `service`, naming one organisation.
Everything it can then read is that organisation's and nothing else — a
token for one organisation is no way into another, whatever the request
names.

The client is looked up on every call. A token lives ten minutes, but
revoking a client should not wait for that: one primary-key read is
cheap next to a revoked service reading a directory.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

from app.middlewares.request_audit import RequestAudit
from app.services.auth.jwt_service import JWTService, TokenUse
from app.services.identity.registry import ClientRegistry

#: The one scope a service token may have without an organisation (§3, §21).
REVOCATIONS_SCOPE = 'revocations'

_bearer = OAuth2PasswordBearer(tokenUrl='/v1/oauth/token', auto_error=False)

_REFUSAL = 'A service token for one organisation is required'


class ServiceCaller(BaseModel):
    """A registered service, acting for one organisation."""

    client_id: str
    name: str
    org_id: str


class ServiceFollower(BaseModel):
    """A registered service following revocations: for one organisation, or (`org_id` None) every one it serves."""

    client_id: str
    name: str
    org_id: str | None


async def require_service(request: Request, token: str | None = Depends(_bearer)) -> ServiceCaller:
    tokens = JWTService()
    payload = tokens.verify(token or '', audience=tokens.issuer, use=TokenUse.SERVICE)
    if payload is None or not payload.get('org'):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=_REFUSAL, headers={'WWW-Authenticate': 'Bearer'}
        )
    client = await ClientRegistry().approved(str(payload['sub']))
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='This client is not approved',
            headers={'WWW-Authenticate': 'Bearer'},
        )
    caller = ServiceCaller(client_id=client.client_id, name=client.name, org_id=str(payload['org']))
    RequestAudit.of(request).attribute_to(user_id=f'client:{client.client_id}', org_id=caller.org_id)
    return caller


async def require_service_follower(request: Request, token: str | None = Depends(_bearer)) -> ServiceFollower:
    """A service token for an organisation, or one with `scope` `revocations` and no organisation.

    The second is what a service obtains at start-up, before it knows an
    organisation to name (§3, §21); it is accepted here and nowhere else —
    `require_service` refuses it for having no `org`.
    """
    tokens = JWTService()
    payload = tokens.verify(token or '', audience=tokens.issuer, use=TokenUse.SERVICE)
    if payload is None or not (payload.get('org') or payload.get('scope') == REVOCATIONS_SCOPE):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=_REFUSAL, headers={'WWW-Authenticate': 'Bearer'}
        )
    client = await ClientRegistry().approved(str(payload['sub']))
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='This client is not approved',
            headers={'WWW-Authenticate': 'Bearer'},
        )
    org_id = str(payload['org']) if payload.get('org') else None
    RequestAudit.of(request).attribute_to(user_id=f'client:{client.client_id}', org_id=org_id)
    return ServiceFollower(client_id=client.client_id, name=client.name, org_id=org_id)
