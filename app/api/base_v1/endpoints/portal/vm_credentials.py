"""VM credentials on the portal: listing and revoking them, and enabling the cloud for an app.

A person sees and revokes their own VM's; an org admin the
organisation's people's and apps'. Never the secret: it is only ever on
the VM (`docs/service-identity.md` §16).
"""

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.base_v1.schema.portal.user import PortalUser
from app.middlewares.request_audit import RequestAudit
from app.security.portal_auth import require_admin
from app.security.producer import ProducerCaller, require_producer
from app.services.cloud.vm_credential_records import VmCredentialList, VmCredentialRecord
from app.services.cloud.vm_credentials import VmCredentials

router = APIRouter()


@router.get('/vm-credentials', response_model=VmCredentialList)
async def list_credentials(caller: ProducerCaller = Depends(require_producer)) -> VmCredentialList:
    """Working VM credentials: the organisation's for an admin, your own otherwise."""
    return await VmCredentials().listed(caller)


@router.delete('/vm-credentials/{credential_id}', status_code=status.HTTP_204_NO_CONTENT)
async def revoke_credential(
    request: Request, credential_id: str, caller: ProducerCaller = Depends(require_producer)
) -> Response:
    """Revoke one: its VM can no longer get tokens, until it is signed in (a person) or re-issued (an app)."""
    await VmCredentials().revoke(caller, credential_id)
    RequestAudit.of(request).set_target(target_type='vm_credential', target_id=credential_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post('/apps/{app_id}/vm-credential', response_model=VmCredentialRecord, status_code=status.HTTP_201_CREATED)
async def enable_app_cloud(
    request: Request, app_id: str, current_user: PortalUser = Depends(require_admin)
) -> VmCredentialRecord:
    """Enable the cloud for an app, or re-issue its VM credential: the old family stops at once.

    The new one waits, encrypted, for the node agent to pick up once when
    it creates the app's VM; the portal never shows it.
    """
    record = await VmCredentials().enable_app(current_user.org_id, app_id, by_user_id=current_user.user_id)
    RequestAudit.of(request).set_target(target_type='vm_credential', target_id=record.credential_id)
    return record
