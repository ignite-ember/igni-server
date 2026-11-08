"""`/v1/apps/{app_id}/vm-profile[-request]`: an app asks for what its VM gets, and reads what it has.

The app's own token only, and only for itself (`docs/service-identity.md`
§12.3). A request changes no VM: an org admin approves or rejects it.
"""

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.base_v1.schema.audit_detail import VmProfileAskedAudit
from app.api.base_v1.schema.vm_profile_requests import VmProfileRequestRecord, VmProfileRequestSettings
from app.api.base_v1.schema.vm_profiles import ProducerVmProfile
from app.middlewares.request_audit import RequestAudit
from app.security.producer import ProducerCaller, require_producer
from app.services.cloud.vm_profile_refusals import VmProfileRefusals
from app.services.cloud.vm_profile_request_refusals import VmProfileRequestRefusals
from app.services.cloud.vm_profile_requests import VmProfileRequests

router = APIRouter(prefix='/apps/{app_id}')


@router.put('/vm-profile-request', response_model=VmProfileRequestRecord, status_code=status.HTTP_200_OK)
async def ask(
    request: Request,
    app_id: str,
    body: VmProfileRequestSettings,
    response: Response,
    caller: ProducerCaller = Depends(require_producer),
) -> VmProfileRequestRecord:
    """201 when new or changed — pending until an org admin decides; 200 when the same as before."""
    with VmProfileRequestRefusals():
        record, changed = await VmProfileRequests().ask_for_app(caller, app_id=app_id, settings=body)
    RequestAudit.of(request).set_target(target_type='vm_profile_request', target_id=record.id)
    RequestAudit.of(request).add_detail(
        VmProfileAskedAudit(
            app_id=app_id,
            version=record.version,
            packages_version=record.packages_version,
            internet=record.requested.internet,
            changed=changed,
            state=record.state,
        )
    )
    if changed:
        response.status_code = status.HTTP_201_CREATED
    return record


@router.get('/vm-profile-request', response_model=VmProfileRequestRecord)
async def read_request(app_id: str, caller: ProducerCaller = Depends(require_producer)) -> VmProfileRequestRecord:
    with VmProfileRequestRefusals():
        return await VmProfileRequests().of_app(caller, app_id)


@router.get('/vm-profile', response_model=ProducerVmProfile)
async def read_profile(app_id: str, caller: ProducerCaller = Depends(require_producer)) -> ProducerVmProfile:
    """What the app's VM gets now: its profile's values, clamped to the caps, and its volume."""
    with VmProfileRequestRefusals(), VmProfileRefusals():
        return await VmProfileRequests().app_profile(caller, app_id)
