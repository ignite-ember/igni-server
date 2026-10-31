"""Item delivery to a producer: a service asks, igni checks and keeps it, the gateway delivers.

An item is an `event`, a `message`, a new `chat` or an `action` on a
chat. Everything below holds for every kind; what a kind may carry
beyond a plain event — text, a thread, attachments — needs the grant
its extension declares in `permissions.service` (`ServiceGrants`).

What is decided (igni's `docs/CLOUD.md` §6, `docs/service-identity.md` §15):

1. A service signs as itself (its `service` token, for one organisation)
   and asks igni to deliver an event to **its own extension** for one
   producer of that organisation — a person or an app.
2. igni refuses unless that producer has the extension on (a person
   through their group, an app through its own list), rate-limits per
   service, records the request, and stores the event until delivered.
3. The gateway, waking the producer's VM, fetches the pending events and
   acknowledges each once the extension has it.

4. An undelivered event expires after `SERVICE_EVENT_MAX_WAIT_SECONDS`
   (14 days), or at the service's earlier `expires_at`; a later one is cut
   to the maximum. An expired event is no longer handed out. The service
   learns what became of each of its events by reading them — `pending`,
   `delivered`, `expired`, or `dropped` with a reason — polling, never
   called back. A closed event is deleted `SERVICE_EVENT_RETENTION_DAYS`
   later by the retention round.

An event always starts or resumes the producer's agent run in its VM,
unattended, for people and apps alike; that is igni's, in the VM. igni
server only carries the event.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import uuid

from fastapi import HTTPException, status
import redis.asyncio as aioredis

from app.api.base_v1.schema.producer_events import (
    AckEvents,
    AttachmentRef,
    ChatAction,
    DeliverItemRequest,
    EventAccepted,
    EventStatus,
    EventStatusList,
    MAX_PAYLOAD_BYTES,
    MAX_VIEW_BYTES,
    PendingEvent,
    PulledEvents,
)
from app.api.base_v1.schema.vm_commands import WakeReason
from app.config import settings
from app.db.redis.keys import key
from app.db.sql.models.producer_event import ProducerEvent
from app.db.sql.models.service_attachment import ServiceAttachment
from app.db.sql.producer_events import AttachmentsUnavailable, ProducerEventsDB
from app.db.sql.service_attachments import ServiceAttachmentsDB
from app.db.sql.tenancy import TenantData
from app.security.producer import ProducerCaller, ProducerRef
from app.security.service_caller import ServiceCaller
from app.services.auth.jwt_service import PERSON
from app.services.cloud.service_grants import ServiceExtensionOf, ServiceGrants
from app.services.cloud.vm_commands import Producer
from app.services.portal.group_extensions import Entitlement, GroupExtensions


class EventRateLimit:
    """Events per service per minute, counted in Redis (`SERVICE_EVENTS_PER_MINUTE`)."""

    async def spend(self, client_id: str) -> int | None:
        """None when allowed; otherwise the seconds until the next minute."""
        limit = settings.service_events_per_minute
        if limit <= 0:
            return None
        now = datetime.now(timezone.utc)
        bucket = key('service_events', client_id, now.strftime('%Y%m%d%H%M'))
        client: aioredis.Redis = aioredis.from_url(settings.redis_url, decode_responses=True)  # type: ignore[assignment]
        try:
            count = int(await client.incr(bucket))
            if count == 1:
                await client.expire(bucket, 120)
        finally:
            await client.aclose()
        return None if count <= limit else 60 - now.second


class ProducerEvents:
    def __init__(self) -> None:
        self._db = ProducerEventsDB()
        self._attachments = ServiceAttachmentsDB()

    @staticmethod
    async def entitlement(org_id: str, producer: ProducerRef) -> Entitlement:
        caller = ProducerCaller(kind=producer.kind, id=producer.id, org_id=org_id)
        return await GroupExtensions().of_producer(caller)

    @staticmethod
    def expiry(requested: datetime | None) -> datetime:
        """The event's `expires_at`: the service's if earlier than the maximum wait, else the maximum.

        Cut rather than refused: a service asking for longer still gets its
        event delivered for as long as the platform allows, and the answer
        says which time was kept.
        """
        now = datetime.now(timezone.utc)
        if requested is not None and requested.tzinfo is None:
            requested = requested.replace(tzinfo=timezone.utc)
        if requested is not None and requested <= now:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='expires_at is already past')
        bound = now + timedelta(seconds=settings.service_event_max_wait_seconds)
        return bound if requested is None else min(requested, bound)

    @staticmethod
    def status_of(event: ProducerEvent) -> EventStatus:
        return EventStatus(
            event_id=event.id,
            producer=ProducerRef(kind=event.producer_kind, id=event.producer_id),  # type: ignore[arg-type]
            kind=event.kind,  # type: ignore[arg-type]
            type=event.event_type,
            thread=event.thread,
            event_key=event.event_key,
            state=event.state,  # type: ignore[arg-type]
            dropped_reason=event.dropped_reason,
            failed_reason=event.failed_reason,
            not_handled_reason=event.not_handled_reason,
            created_at=event.created_at,
            expires_at=event.expires_at,
            delivered_at=event.delivered_at,
            updated_at=event.updated_at,
        )

    async def status(self, caller: ServiceCaller, event_id: str) -> EventStatus:
        """One of the calling service's own events; another service's is a 404."""
        await self._db.expire_due(caller.org_id)
        event = await self._db.of_client(caller.org_id, caller.client_id, event_id)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No such event from this service')
        return self.status_of(event)

    async def sent(
        self, caller: ServiceCaller, *, state: str | None, since: datetime | None, limit: int
    ) -> EventStatusList:
        await self._db.expire_due(caller.org_id)
        rows = await self._db.sent_by(caller.org_id, caller.client_id, state=state, since=since, limit=limit)
        return EventStatusList(events=[self.status_of(row) for row in rows])

    async def close_out(self) -> int:
        """The retention round: mark what is due as expired, delete what closed long ago."""
        purged = 0
        for org_id in await TenantData().each_tenant():
            await self._db.expire_due(org_id)
            if settings.service_event_retention_days > 0:
                purged += await self._db.purge(org_id, older_than_days=settings.service_event_retention_days)
            await self._attachments.purge_expired(org_id)
        return purged

    @staticmethod
    def _within_limits(body: DeliverItemRequest) -> None:
        if len(json.dumps(body.payload)) > MAX_PAYLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f'A payload may be at most {MAX_PAYLOAD_BYTES} bytes',
            )
        view = body.action.view if body.action is not None else None
        if view is not None and len(json.dumps(view)) > MAX_VIEW_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f'A view may be at most {MAX_VIEW_BYTES} bytes',
            )

    @staticmethod
    async def spend(caller: ServiceCaller) -> None:
        """One request's worth of the service's minute; a 429 past it."""
        wait = await EventRateLimit().spend(caller.client_id)
        if wait is not None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail='Too many events from this service this minute',
                headers={'Retry-After': str(wait)},
            )

    async def deliver(self, caller: ServiceCaller, body: DeliverItemRequest) -> EventAccepted:
        """Store one item for one producer, after every check, with its attachments bound to it."""
        self._within_limits(body)
        await self.spend(caller)
        extension = await ServiceExtensionOf.caller(caller)
        ServiceGrants.require(extension, body.needs())
        await Producer.exists(caller.org_id, body.producer)
        if not (await self.entitlement(caller.org_id, body.producer)).allows(extension.extension_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f'{extension.extension_id} is not on for this producer',
            )
        try:
            stored, duplicate = await self._db.add(
                caller.org_id,
                ProducerEvent(
                    id=str(uuid.uuid4()),
                    client_id=caller.client_id,
                    extension_id=extension.extension_id,
                    producer_kind=body.producer.kind,
                    producer_id=body.producer.id,
                    kind=body.kind,
                    event_type=body.stored_type,
                    payload=body.payload,
                    thread=body.thread,
                    text=body.text,
                    title=body.title,
                    start_run=body.start_run,
                    action=body.action.model_dump(mode='json', exclude_none=True) if body.action else None,
                    attachment_ids=body.attachments,
                    event_key=body.event_key,
                    expires_at=self.expiry(body.expires_at),
                ),
            )
        except AttachmentsUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return EventAccepted(event_id=stored.id, expires_at=stored.expires_at, duplicate=duplicate)

    async def pull(self, org_id: str, producer: ProducerRef) -> PulledEvents:
        await Producer.exists(org_id, producer)
        entitlement = await self.entitlement(org_id, producer)

        async def still_enabled(extension_id: str) -> bool:
            return entitlement.allows(extension_id)

        rows = await self._db.pull(
            org_id, producer_kind=producer.kind, producer_id=producer.id, still_enabled=still_enabled
        )
        named = await self._attachments.described(
            org_id, [attachment for row in rows for attachment in (row.attachment_ids or [])]
        )
        return PulledEvents(events=[self.pending(row, named) for row in rows])

    @staticmethod
    def pending(row: ProducerEvent, named: dict[str, ServiceAttachment]) -> PendingEvent:
        """An item as the producer's igni receives it, its attachments described, not carried."""
        return PendingEvent(
            event_id=row.id,
            extension_id=row.extension_id,
            client_id=row.client_id,
            kind=row.kind,  # type: ignore[arg-type]
            type=row.event_type,
            payload=dict(row.payload or {}),
            thread=row.thread,
            text=row.text,
            title=row.title,
            start_run=row.start_run,
            action=ChatAction.model_validate(row.action) if row.action else None,
            attachments=[
                AttachmentRef(
                    attachment_id=held.id,
                    filename=held.filename,
                    content_type=held.content_type,
                    size=held.size,
                    sha256=held.sha256,
                )
                for held in (named.get(i) for i in (row.attachment_ids or []))
                if held is not None
            ],
            event_key=row.event_key,
            created_at=row.created_at,
            expires_at=row.expires_at,
        )

    async def ack(self, org_id: str, body: AckEvents) -> int:
        """Delivered — or, for an event no handler took, not handled, with the reason."""
        return await self._db.ack(org_id, body.event_ids, not_handled=self.not_handled(body))

    @staticmethod
    def not_handled(body: AckEvents) -> dict[str, str]:
        return {missed.event_id: missed.reason for missed in body.not_handled}

    async def fail(self, org_id: str, event_ids: list[str], reason: str) -> int:
        """The gateway gave up delivering these after its retries."""
        return await self._db.fail(org_id, event_ids, reason)

    @staticmethod
    def _person(caller: ProducerCaller) -> str:
        """The organisation of a signed-in person; an app's events go through the gateway, never here."""
        if caller.kind != PERSON or not caller.org_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail='Only a person fetches their own events here'
            )
        return caller.org_id

    async def pull_mine(self, caller: ProducerCaller) -> PulledEvents:
        """The person's own events, for igni on their desktop: the gateway's pull, with the same lease."""
        return await self.pull(self._person(caller), caller.ref)

    async def ack_mine(self, caller: ProducerCaller, body: AckEvents) -> int:
        """Acknowledge the person's own events; anybody else's ids are left alone."""
        return await self._db.ack(
            self._person(caller),
            body.event_ids,
            not_handled=self.not_handled(body),
            producer_kind=caller.kind,
            producer_id=caller.id,
        )

    async def wakes(self) -> list[WakeReason]:
        found = []
        for org_id in await TenantData().each_tenant():
            for kind, producer_id in await self._db.waiting(org_id):
                found.append(
                    WakeReason(
                        org_id=org_id,
                        producer=ProducerRef(kind=kind, id=producer_id),  # type: ignore[arg-type]
                        reasons=['event'],
                    )
                )
        return found
