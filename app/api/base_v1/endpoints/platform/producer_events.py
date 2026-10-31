"""The gateway's side of event delivery: fetch a producer's pending events, acknowledge them.

A platform token only (`docs/service-identity.md` §15). An event handed
out and not acknowledged within a short lease is handed out again, so a
gateway that dies mid-delivery loses nothing; the extension receives an
event at least once and can tell a repeat by `event_id`.
"""

from fastapi import APIRouter, Depends, Request

from app.api.base_v1.schema.producer_events import AckedEvents, AckEvents, FailedEvents, FailEvents, PulledEvents
from app.middlewares.audit import set_audit_detail
from app.security.platform_caller import PlatformCaller, require_platform
from app.security.producer import ProducerKind, ProducerRef
from app.services.cloud.producer_events import ProducerEvents

router = APIRouter()


@router.post('/orgs/{org_id}/producers/{kind}/{producer_id}/events/pull', response_model=PulledEvents)
async def pull(
    org_id: str, kind: ProducerKind, producer_id: str, _platform: PlatformCaller = Depends(require_platform)
) -> PulledEvents:
    return await ProducerEvents().pull(org_id, ProducerRef(kind=kind, id=producer_id))


@router.post('/orgs/{org_id}/events/ack', response_model=AckedEvents)
async def ack(
    request: Request, org_id: str, body: AckEvents, _platform: PlatformCaller = Depends(require_platform)
) -> AckedEvents:
    """Delivered — and the events no handler took, `not_handled` with their reasons. Audited."""
    if body.not_handled:
        set_audit_detail(request, {'not_handled': [m.model_dump() for m in body.not_handled]})
    return AckedEvents(delivered=await ProducerEvents().ack(org_id, body))


@router.post('/orgs/{org_id}/events/fail', response_model=FailedEvents)
async def fail(
    request: Request, org_id: str, body: FailEvents, _platform: PlatformCaller = Depends(require_platform)
) -> FailedEvents:
    """Give up on events the gateway could not deliver: failed, never handed out or woken for again. Audited."""
    set_audit_detail(request, {'event_ids': body.event_ids, 'reason': body.reason})
    return FailedEvents(failed=await ProducerEvents().fail(org_id, body.event_ids, body.reason))
