"""The deployment's single licence high-water mark, for the modules and the gateway.

igni server holds the one mark (the latest time the deployment has seen)
in its encrypted integrity file. The paid modules and the Fleet gateway no
longer keep their own; they read this value over their existing link and
cache it, so each component evaluates the licence at the same
`max(now, mark − 24h, issued)` and a clock wound back on one host does not
revive a lapsed licence.

Any approved registered client may read it: the Fleet gateway is a
`platform` client, and CodeIndex and Artifacts are `service` clients. The
mark is a single deployment-wide timestamp, not an organisation's data, so
it carries the same clearance as approval itself — but it is not public:
an unapproved or revoked client is refused.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

from app.middlewares.request_audit import RequestAudit
from app.services.auth.jwt_service import JWTService, TokenUse
from app.services.deployment_licence import deployment_licence, DeploymentLicence
from app.services.identity.registry import ClientRegistry

router = APIRouter()

_bearer = OAuth2PasswordBearer(tokenUrl='/v1/oauth/token', auto_error=False)


class CentralMark(BaseModel):
    """The deployment's high-water mark. `seen` is None before the first time is recorded."""

    seen: datetime | None = None


#: The token kinds that may read the mark: the Fleet gateway (`platform`), and the paid modules,
#: which call igni with their `client_credentials` token (`service`, or an `app` token per organisation).
_READERS = ((TokenUse.PLATFORM, 'platform'), (TokenUse.SERVICE, 'service'), (TokenUse.APP, 'app'))


async def require_registered_client(request: Request, token: str | None = Depends(_bearer)) -> str:
    """Any approved client calling igni for itself — the gateway, or a paid module. Returns the
    client id. Refuses an unapproved or revoked client (the mark is approval-gated, not public)."""
    tokens = JWTService()
    registry = ClientRegistry()
    for use, kind in _READERS:
        payload = tokens.verify(token or '', audience=tokens.issuer, use=use)
        if payload is None:
            continue
        client = await registry.approved(payload.sub, kind=kind)
        if client is not None:
            RequestAudit.of(request).attribute_to(user_id=f'client:{client.client_id}')
            return client.client_id
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail='An approved platform, service or app client is required',
        headers={'WWW-Authenticate': 'Bearer'},
    )


@router.get('/licence/clock', response_model=CentralMark)
async def central_mark(
    _client: str = Depends(require_registered_client),
    licence: DeploymentLicence = Depends(deployment_licence),
) -> CentralMark:
    return CentralMark(seen=licence.high_water.seen)
