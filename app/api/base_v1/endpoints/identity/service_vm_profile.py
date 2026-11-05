"""`GET /v1/service-vm-profile`: what a service's extension asked its VMs to get, in one organisation.

With a service token, for the token's organisation. The extension asks
through its manifest's `vm`; the organisation's admin decides
(`docs/service-identity.md` §12.3).
"""

from fastapi import APIRouter, Depends

from app.api.base_v1.schema.vm_profile_requests import ServiceVmProfile
from app.security.service_caller import require_service, ServiceCaller
from app.services.cloud.vm_profile_requests import VmProfileRequests

router = APIRouter()


@router.get('/service-vm-profile', response_model=ServiceVmProfile, tags=['Directory'])
async def read(caller: ServiceCaller = Depends(require_service)) -> ServiceVmProfile:
    return await VmProfileRequests().for_service(caller)
