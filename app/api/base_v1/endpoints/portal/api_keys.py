"""Personal API keys on the portal: make one, see them, revoke one (`docs/service-identity.md` §22).

Everybody in an organisation may make keys for themselves; an org admin
also sees and revokes everybody's. None of this is reachable with a
session an API key bought: a key does not make keys.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.base_v1.schema.portal.user import PortalUser
from app.middlewares.request_audit import RequestAudit
from app.security.api_key_sessions import no_api_key_session
from app.security.portal_auth import get_current_portal_user
from app.services.identity.api_keys import ApiKeyList, ApiKeys, CreateApiKey, CreatedApiKey

router = APIRouter(dependencies=[Depends(no_api_key_session)])


@router.get('/api-keys', response_model=ApiKeyList)
async def list_keys(current_user: PortalUser = Depends(get_current_portal_user)) -> ApiKeyList:
    """Working API keys: the organisation's for an admin, your own otherwise."""
    return await ApiKeys().listed(current_user)


@router.post('/api-keys', response_model=CreatedApiKey, status_code=status.HTTP_201_CREATED)
async def create_key(
    request: Request, body: CreateApiKey, current_user: PortalUser = Depends(get_current_portal_user)
) -> CreatedApiKey:
    """A new key for you. The answer is the only time the key is shown."""
    created = await ApiKeys().create(current_user, body)
    RequestAudit.of(request).set_target(target_type='api_key', target_id=created.key_id)
    return created


@router.delete('/api-keys/{key_id}', status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    request: Request, key_id: str, current_user: PortalUser = Depends(get_current_portal_user)
) -> Response:
    """Revoke a key: yours, or anybody's for an org admin. No new sessions from it."""
    await ApiKeys().revoke(current_user, key_id)
    RequestAudit.of(request).set_target(target_type='api_key', target_id=key_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
