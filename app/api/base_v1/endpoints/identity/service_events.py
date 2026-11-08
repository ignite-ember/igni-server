"""A service sending its extension, for a producer, an item for the agent: an event, a message, a chat, an action.

`POST /v1/service-events` takes an event; `POST /v1/service-items` takes
any kind; `POST /v1/service-attachments` uploads a file an item then
names. With a service token, for the token's organisation only. Refused
unless the producer has the service's extension on, and unless that
extension declares the grant the item needs (`permissions.service`);
rate-limited per service; recorded by the audit middleware and as the
stored item (`docs/service-identity.md` §15).

The service reads what became of its own items — pending, delivered,
expired, failed, or dropped with a reason — by polling the two `GET`s below.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.base_v1.schema.audit_detail import AttachmentUploadedAudit, ItemDeliveredAudit
from app.api.base_v1.schema.producer_events import (
    AttachmentStored,
    DeliverEventRequest,
    DeliverItemRequest,
    EventAccepted,
    EventState,
    EventStatus,
    EventStatusList,
    MAX_ATTACHMENT_BYTES,
)
from app.middlewares.request_audit import RequestAudit
from app.security.service_caller import require_service, ServiceCaller
from app.services.cloud.producer_event_refusals import ProducerEventRefusals
from app.services.cloud.producer_events import ProducerEvents
from app.services.cloud.service_attachments import ServiceAttachments

router = APIRouter()


@router.post('/service-events', response_model=EventAccepted, status_code=status.HTTP_202_ACCEPTED, tags=['Directory'])
async def deliver(
    request: Request, body: DeliverEventRequest, caller: ServiceCaller = Depends(require_service)
) -> EventAccepted:
    return await _deliver(request, body.item(), caller)


@router.post('/service-items', response_model=EventAccepted, status_code=status.HTTP_202_ACCEPTED, tags=['Directory'])
async def deliver_item(
    request: Request, body: DeliverItemRequest, caller: ServiceCaller = Depends(require_service)
) -> EventAccepted:
    """Any kind of item: `event`, `message`, `chat` (a new chat for a thread) or `action` on a thread's chat."""
    return await _deliver(request, body, caller)


async def _deliver(request: Request, body: DeliverItemRequest, caller: ServiceCaller) -> EventAccepted:
    RequestAudit.of(request).add_detail(
        ItemDeliveredAudit(
            producer=f'{body.producer.kind}:{body.producer.id}',
            kind=body.kind,
            type=body.stored_type,
            thread=body.thread,
            attachments=body.attachments,
        )
    )
    with ProducerEventRefusals():
        accepted = await ProducerEvents().deliver(caller, body)
    RequestAudit.of(request).set_target(target_type='producer_event', target_id=accepted.event_id)
    return accepted


@router.post(
    '/service-attachments',
    response_model=AttachmentStored,
    status_code=status.HTTP_201_CREATED,
    tags=['Directory'],
)
async def upload_attachment(
    request: Request,
    filename: str = Query(min_length=1, max_length=200),
    caller: ServiceCaller = Depends(require_service),
) -> AttachmentStored:
    """The bytes are the body, `Content-Type` their media type; at most 5 MiB."""
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f'An attachment may be at most {MAX_ATTACHMENT_BYTES} bytes',
            )
    RequestAudit.of(request).add_detail(AttachmentUploadedAudit(filename=filename, size=len(content)))
    with ProducerEventRefusals():
        stored = await ServiceAttachments().upload(
            caller, filename=filename, content_type=request.headers.get('content-type'), content=bytes(content)
        )
    RequestAudit.of(request).set_target(target_type='service_attachment', target_id=stored.attachment_id)
    return stored


@router.get('/service-events', response_model=EventStatusList, tags=['Directory'])
async def sent(
    state: EventState | None = None,
    since: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    caller: ServiceCaller = Depends(require_service),
) -> EventStatusList:
    """This service's own events, oldest change first; `since` is the last `updated_at` it saw."""
    return await ProducerEvents().sent(caller, state=state, since=since, limit=limit)


@router.get('/service-events/{event_id}', response_model=EventStatus, tags=['Directory'])
async def event_status(event_id: str, caller: ServiceCaller = Depends(require_service)) -> EventStatus:
    with ProducerEventRefusals():
        return await ProducerEvents().status(caller, event_id)
