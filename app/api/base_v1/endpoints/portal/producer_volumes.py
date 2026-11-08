"""One producer's volume: read it, grow it (`docs/service-identity.md` §12.1).

The organisation is in the path: an org admin in theirs, the super admin
in any. Every grow is in the audit trail with the size asked for.
"""

from fastapi import APIRouter, Depends, Request

from app.api.base_v1.schema.audit_detail import VolumeGrownAudit
from app.api.base_v1.schema.vm_profiles import GrowVolume, ProducerVolume
from app.middlewares.request_audit import RequestAudit
from app.security.org_authority import OrgAuthority, require_org_authority
from app.security.producer import ProducerKind, ProducerRef
from app.services.cloud.producer_volumes import ProducerVolumes

router = APIRouter()


@router.get('/orgs/{org_id}/producers/{kind}/{producer_id}/volume', response_model=ProducerVolume)
async def read(
    kind: ProducerKind, producer_id: str, authority: OrgAuthority = Depends(require_org_authority)
) -> ProducerVolume:
    return await ProducerVolumes().read(authority.org_id, ProducerRef(kind=kind, id=producer_id))


@router.post('/orgs/{org_id}/producers/{kind}/{producer_id}/volume', response_model=ProducerVolume)
async def grow(
    request: Request,
    kind: ProducerKind,
    producer_id: str,
    body: GrowVolume,
    authority: OrgAuthority = Depends(require_org_authority),
) -> ProducerVolume:
    """Grow it to `gib`: never smaller than now, at most the profile's `volume_max_gib`."""
    RequestAudit.of(request).set_target(target_type=f'{kind}_volume', target_id=producer_id)
    RequestAudit.of(request).add_detail(VolumeGrownAudit(gib=body.gib))
    return await ProducerVolumes().grow(authority, ProducerRef(kind=kind, id=producer_id), body.gib)
