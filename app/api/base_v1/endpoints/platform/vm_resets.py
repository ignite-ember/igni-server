"""An app's VM erased after inactivity, and an erasure done (`docs/service-identity.md` §12.2, §18.3).

A platform token — the gateway's.

* `.../vm-resets/check` answers whether an app's or a space's VM may be
  erased after inactivity now: 204, or 409 saying why (a person's VM, the
  setting off, a legal hold, another life).
* `.../vm-resets` reports one done: a row in the organisation's audit
  trail, and the VM credential issued again for the next fresh VM.
* `.../erasures` reports a removed or discarded incarnation's erasure
  done: a row in the trail, and the portal shows it erased.
"""

from fastapi import APIRouter, Depends, Response, status

from app.api.base_v1.schema.retention import ErasureDoneBody, ErasureRecord
from app.api.base_v1.schema.vm_resets import VmResetBody, VmResetResponse
from app.security.platform_caller import PlatformCaller, require_platform
from app.security.producer import ProducerKind, ProducerRef
from app.services.cloud.erasures import Erasures
from app.services.cloud.vm_resets import VmResets

router = APIRouter()


@router.post('/orgs/{org_id}/producers/{kind}/{producer_id}/vm-resets/check', status_code=status.HTTP_204_NO_CONTENT)
async def check(
    org_id: str,
    kind: ProducerKind,
    producer_id: str,
    body: VmResetBody,
    _platform: PlatformCaller = Depends(require_platform),
) -> Response:
    await VmResets().check(org_id, ProducerRef(kind=kind, id=producer_id), body)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post('/orgs/{org_id}/producers/{kind}/{producer_id}/vm-resets', response_model=VmResetResponse)
async def report(
    org_id: str,
    kind: ProducerKind,
    producer_id: str,
    body: VmResetBody,
    platform: PlatformCaller = Depends(require_platform),
) -> VmResetResponse:
    return await VmResets().record(org_id, ProducerRef(kind=kind, id=producer_id), body, by=platform)


@router.post('/orgs/{org_id}/producers/{kind}/{producer_id}/erasures', response_model=ErasureRecord)
async def erasure_done(
    org_id: str,
    kind: ProducerKind,
    producer_id: str,
    body: ErasureDoneBody,
    platform: PlatformCaller = Depends(require_platform),
) -> ErasureRecord:
    return await Erasures().done(org_id, ProducerRef(kind=kind, id=producer_id), body, by_client_id=platform.client_id)
