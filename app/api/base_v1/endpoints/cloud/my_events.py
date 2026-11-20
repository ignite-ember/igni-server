"""`/v1/me/events`: a signed-in person's own events, for igni on their desktop.

The same delivery the gateway does for a cloud VM (`docs/service-identity.md`
§15): fetch what is pending, oldest first, under a two-minute lease, and
acknowledge what igni took. An event not acknowledged is handed out
again; igni tells a repeat by `event_id`.

One side owns a person's events (igni-core `docs/design/local-cloud-switch.md`
§7): while it is their cloud VM, a pull here is a 409
`{"error": "events_owned_elsewhere", "owner": "cloud"}`, which igni on the
desktop reads as "idle and ask again later". Acknowledging and fetching
attachments stay open for what was leased before.

An item's attachments are fetched here by whichever igni delivers it —
the desktop with the person's session, a VM with its own token (§16) —
so a person's or an app's token is taken, for that producer's own items.
"""

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from app.api.base_v1.schema.audit_detail import EventsNotHandledAudit
from app.api.base_v1.schema.producer_events import AckedEvents, AckEvents, PulledEvents
from app.middlewares.request_audit import RequestAudit
from app.security.producer import ProducerCaller, require_producer
from app.services.cloud.events_owner_errors import EventsOwnerRefused
from app.services.cloud.events_owner_refusals import EventsOwnerRefusals
from app.services.cloud.producer_event_refusals import ProducerEventRefusals
from app.services.cloud.producer_events import ProducerEvents
from app.services.cloud.service_attachments import ServiceAttachments

router = APIRouter(prefix='/me/events')


@router.post(
    '/pull',
    response_model=PulledEvents,
    responses={409: {'description': '`{"error": "events_owned_elsewhere", "owner": "cloud"}`'}},
)
async def pull(caller: ProducerCaller = Depends(require_producer)) -> PulledEvents | JSONResponse:
    try:
        with ProducerEventRefusals():
            return await ProducerEvents().pull_mine(caller)
    except EventsOwnerRefused as exc:
        return EventsOwnerRefusals.response(exc)


@router.post('/ack', response_model=AckedEvents)
async def ack(request: Request, body: AckEvents, caller: ProducerCaller = Depends(require_producer)) -> AckedEvents:
    """Delivered — and the events no handler took, `not_handled` with their reasons. Audited."""
    if body.not_handled:
        RequestAudit.of(request).add_detail(EventsNotHandledAudit(not_handled=body.not_handled))
    with ProducerEventRefusals():
        return AckedEvents(delivered=await ProducerEvents().ack_mine(caller, body))


@router.get('/{event_id}/attachments/{attachment_id}', response_class=Response)
async def attachment(event_id: str, attachment_id: str, caller: ProducerCaller = Depends(require_producer)) -> Response:
    """The bytes of one attachment of one of the caller's own items, while the item is unexpired."""
    held = await ServiceAttachments().for_producer(caller, event_id=event_id, attachment_id=attachment_id)
    return Response(
        content=held.content,
        media_type=held.content_type,
        headers={'X-Igni-Attachment-Sha256': held.sha256, 'Cache-Control': 'no-store'},
    )
