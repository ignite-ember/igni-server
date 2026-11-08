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

from app.api.base_v1.schema.producer_events import (
    AckEvents,
    DeliverItemRequest,
    EventAccepted,
    EventStatus,
    EventStatusList,
    MAX_PAYLOAD_BYTES,
    MAX_VIEW_BYTES,
    PulledEvents,
)
from app.api.base_v1.schema.vm_commands import WakeReason
from app.config import settings
from app.db.sql.models.producer_event import ProducerEvent
from app.db.sql.producer_events import ProducerEventsDB
from app.db.sql.service_attachments import ServiceAttachmentsDB
from app.db.sql.tenant_registry import TenantRegistryDB
from app.security.producer import ProducerCaller, ProducerRef
from app.security.service_caller import ServiceCaller
from app.services.auth.jwt_service import PERSON
from app.services.cloud.event_rate_limit import EventRateLimit
from app.services.cloud.producer_event_errors import (
    ExpiryPast,
    ExtensionOff,
    NoSuchEvent,
    NotAPerson,
    PayloadTooLarge,
    ViewTooLarge,
)
from app.services.cloud.producer_event_views import ProducerEventViews
from app.services.cloud.producer_lookup import ProducerLookup
from app.services.cloud.service_grants import ServiceExtensionOf, ServiceGrants
from app.services.portal.group_extensions import Entitlement, GroupExtensions


class ProducerEvents:
    """Raises `EventRefused` subclasses and the store's `AttachmentsUnavailable`.

    `ProducerEventRefusals` makes HTTP of them.
    """

    def __init__(
        self,
        *,
        db: ProducerEventsDB | None = None,
        attachments: ServiceAttachmentsDB | None = None,
        tenants: TenantRegistryDB | None = None,
        rate_limit: EventRateLimit | None = None,
        group_extensions: GroupExtensions | None = None,
        extension_of: ServiceExtensionOf | None = None,
        producers: ProducerLookup | None = None,
    ) -> None:
        self._db = db or ProducerEventsDB()
        self._attachments = attachments or ServiceAttachmentsDB()
        self._tenants = tenants or TenantRegistryDB()
        self._rate_limit = rate_limit or EventRateLimit()
        self._group_extensions = group_extensions or GroupExtensions()
        self._extension_of = extension_of or ServiceExtensionOf()
        self._producers = producers or ProducerLookup()

    async def entitlement(self, org_id: str, producer: ProducerRef) -> Entitlement:
        caller = ProducerCaller(kind=producer.kind, id=producer.id, org_id=org_id)
        return await self._group_extensions.of_producer(caller)

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
            raise ExpiryPast()
        bound = now + timedelta(seconds=settings.service_event_max_wait_seconds)
        return bound if requested is None else min(requested, bound)

    async def status(self, caller: ServiceCaller, event_id: str) -> EventStatus:
        """One of the calling service's own events; another service's is `NoSuchEvent`."""
        await self._db.expire_due(caller.org_id)
        event = await self._db.of_client(caller.org_id, caller.client_id, event_id)
        if event is None:
            raise NoSuchEvent()
        return ProducerEventViews.status(event)

    async def sent(
        self, caller: ServiceCaller, *, state: str | None, since: datetime | None, limit: int
    ) -> EventStatusList:
        await self._db.expire_due(caller.org_id)
        rows = await self._db.sent_by(caller.org_id, caller.client_id, state=state, since=since, limit=limit)
        return EventStatusList(events=[ProducerEventViews.status(row) for row in rows])

    async def close_out(self) -> int:
        """The retention round: mark what is due as expired, delete what closed long ago."""
        purged = 0
        for org_id in await self._tenants.active_org_ids():
            await self._db.expire_due(org_id)
            if settings.service_event_retention_days > 0:
                purged += await self._db.purge(org_id, older_than_days=settings.service_event_retention_days)
            await self._attachments.purge_expired(org_id)
        return purged

    @staticmethod
    def _within_limits(body: DeliverItemRequest) -> None:
        if len(json.dumps(body.payload)) > MAX_PAYLOAD_BYTES:
            raise PayloadTooLarge()
        view = body.action.view if body.action is not None else None
        if view is not None and len(json.dumps(view)) > MAX_VIEW_BYTES:
            raise ViewTooLarge()

    async def deliver(self, caller: ServiceCaller, body: DeliverItemRequest) -> EventAccepted:
        """Store one item for one producer, after every check, with its attachments bound to it."""
        self._within_limits(body)
        await self._rate_limit.spend(caller.client_id)
        extension = await self._extension_of.caller(caller)
        ServiceGrants.require(extension, body.needs())
        await self._producers.exists(caller.org_id, body.producer)
        if not (await self.entitlement(caller.org_id, body.producer)).allows(extension.extension_id):
            raise ExtensionOff(extension.extension_id)
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
        return EventAccepted(event_id=stored.id, expires_at=stored.expires_at, duplicate=duplicate)

    async def pull(self, org_id: str, producer: ProducerRef) -> PulledEvents:
        await self._producers.exists(org_id, producer)
        entitlement = await self.entitlement(org_id, producer)

        async def still_enabled(extension_id: str) -> bool:
            return entitlement.allows(extension_id)

        rows = await self._db.pull(
            org_id, producer_kind=producer.kind, producer_id=producer.id, still_enabled=still_enabled
        )
        named = await self._attachments.described(
            org_id, [attachment for row in rows for attachment in (row.attachment_ids or [])]
        )
        return PulledEvents(events=[ProducerEventViews.pending(row, named) for row in rows])

    async def ack(self, org_id: str, body: AckEvents) -> int:
        """Delivered — or, for an event no handler took, not handled, with the reason."""
        return await self._db.ack(org_id, body.event_ids, not_handled=body.not_handled_reasons())

    async def fail(self, org_id: str, event_ids: list[str], reason: str) -> int:
        """The gateway gave up delivering these after its retries."""
        return await self._db.fail(org_id, event_ids, reason)

    @staticmethod
    def _person(caller: ProducerCaller) -> str:
        """The organisation of a signed-in person; an app's events go through the gateway, never here."""
        if caller.kind != PERSON or not caller.org_id:
            raise NotAPerson()
        return caller.org_id

    async def pull_mine(self, caller: ProducerCaller) -> PulledEvents:
        """The person's own events, for igni on their desktop: the gateway's pull, with the same lease."""
        return await self.pull(self._person(caller), caller.ref)

    async def ack_mine(self, caller: ProducerCaller, body: AckEvents) -> int:
        """Acknowledge the person's own events; anybody else's ids are left alone."""
        return await self._db.ack(
            self._person(caller),
            body.event_ids,
            not_handled=body.not_handled_reasons(),
            producer_kind=caller.kind,
            producer_id=caller.id,
        )

    async def wakes(self) -> list[WakeReason]:
        found = []
        for org_id in await self._tenants.active_org_ids():
            for kind, producer_id in await self._db.waiting(org_id):
                found.append(
                    WakeReason(org_id=org_id, producer=ProducerRef.stored(kind, producer_id), reasons=['event'])
                )
        return found
