"""The node agent's one pickup of an app's or a space's VM credential.

A platform token. Answers once per issued credential — when the node
agent first creates the app's VM — and 404 ever after; audited. It is
the only way a credential leaves igni server, and no platform route
hands out a token for a producer (`docs/service-identity.md` §16).
"""

from fastapi import APIRouter, Depends, Request

from app.middlewares.audit import set_audit_detail, set_audit_target
from app.security.platform_caller import PlatformCaller, require_platform
from app.security.producer import ProducerKind, ProducerRef
from app.services.cloud.vm_credentials import PickedUp, VmCredentials

router = APIRouter()


@router.post('/orgs/{org_id}/producers/{kind}/{producer_id}/vm-credential/pickup', response_model=PickedUp)
async def pick_up(
    request: Request,
    org_id: str,
    kind: ProducerKind,
    producer_id: str,
    platform: PlatformCaller = Depends(require_platform),
) -> PickedUp:
    """An app's or a space's; a person's is a 404 — they sign in instead."""
    picked = await VmCredentials().pick_up(
        org_id, ProducerRef(kind=kind, id=producer_id), by_client_id=platform.client_id
    )
    set_audit_target(request, target_type='vm_credential', target_id=picked.credential_id)
    set_audit_detail(request, {kind: producer_id, 'org': org_id})
    return picked
