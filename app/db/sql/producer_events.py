"""Items waiting for producers, in the organisation's own database."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.db.sql.bind_chunks import chunks
from app.db.sql.models.producer_event import (
    DELIVERED,
    DROPPED,
    EXPIRED,
    EXTENSION_OFF,
    FAILED,
    NOT_HANDLED,
    PENDING,
    ProducerEvent,
)
from app.db.sql.models.service_attachment import ServiceAttachment
from app.db.sql.tenancy import TenantData

#: An event handed to the gateway and not acknowledged within this is
#: handed out again.
LEASE = timedelta(minutes=2)


class AttachmentsUnavailable(Exception):
    """An item named attachments that are not this service's, are gone, or another item already names."""


class ProducerEventsDB(TenantData):
    async def add(self, org_id: str, event: ProducerEvent) -> tuple[ProducerEvent, bool]:
        """Store it; with an `event_key` already stored, the stored one and True.

        The attachments it names (`attachment_ids`) are bound to it in the
        same transaction, and expire with it. Raises `AttachmentsUnavailable`,
        storing nothing, when one is not the sending service's, is gone, or
        is named by another item already.
        """
        async with self.session_scope(org_id) as session:
            if event.event_key is not None:
                found = (
                    (
                        await session.execute(
                            select(ProducerEvent).where(
                                ProducerEvent.extension_id == event.extension_id,
                                ProducerEvent.producer_kind == event.producer_kind,
                                ProducerEvent.producer_id == event.producer_id,
                                ProducerEvent.event_key == event.event_key,
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if found is not None:
                    return found, True
            stored = await session.execute(
                insert(ProducerEvent)
                .values(
                    id=event.id,
                    client_id=event.client_id,
                    extension_id=event.extension_id,
                    producer_kind=event.producer_kind,
                    producer_id=event.producer_id,
                    kind=event.kind,
                    event_type=event.event_type,
                    payload=event.payload,
                    thread=event.thread,
                    text=event.text,
                    title=event.title,
                    start_run=event.start_run,
                    action=event.action,
                    attachment_ids=list(event.attachment_ids or []),
                    event_key=event.event_key,
                    state=PENDING,
                    expires_at=event.expires_at,
                )
                .on_conflict_do_nothing()
                .returning(ProducerEvent.id)
            )
            if stored.first() is None:
                raced = (
                    (
                        await session.execute(
                            select(ProducerEvent).where(
                                ProducerEvent.extension_id == event.extension_id,
                                ProducerEvent.producer_kind == event.producer_kind,
                                ProducerEvent.producer_id == event.producer_id,
                                ProducerEvent.event_key == event.event_key,
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                assert raced is not None
                return raced, True
            await self._bind(session, event)
        return event, False

    @staticmethod
    async def _bind(session, event: ProducerEvent) -> None:  # noqa: ANN001 — the open AsyncSession
        """Name the item's attachments as its own: they expire with it."""
        ids = list(event.attachment_ids or [])
        if not ids:
            return
        now = datetime.now(timezone.utc)
        bound = await session.execute(
            update(ServiceAttachment)
            .where(
                ServiceAttachment.id.in_(ids),
                ServiceAttachment.client_id == event.client_id,
                ServiceAttachment.event_id.is_(None),
                ServiceAttachment.expires_at > now,
            )
            .values(event_id=event.id, expires_at=event.expires_at)
        )
        if int(bound.rowcount or 0) != len(ids):
            raise AttachmentsUnavailable(
                'an attachment is not one this service uploaded, has expired, or another item names it'
            )

    @staticmethod
    def _deliverable(now: datetime):  # noqa: ANN205 — a SQLAlchemy clause
        return and_(
            ProducerEvent.state == PENDING,
            or_(ProducerEvent.expires_at.is_(None), ProducerEvent.expires_at > now),
        )

    async def pull(
        self,
        org_id: str,
        *,
        producer_kind: str,
        producer_id: str,
        still_enabled: Callable[[str], Awaitable[bool]],
        limit: int = 100,
    ) -> list[ProducerEvent]:
        """Hand out what is pending and unexpired, oldest first; drop what its extension no longer reaches."""
        now = datetime.now(timezone.utc)
        async with self.session_scope(org_id) as session:
            rows = (
                (
                    await session.execute(
                        select(ProducerEvent)
                        .where(
                            ProducerEvent.producer_kind == producer_kind,
                            ProducerEvent.producer_id == producer_id,
                            self._deliverable(now),
                            or_(ProducerEvent.handed_out_at.is_(None), ProducerEvent.handed_out_at < now - LEASE),
                        )
                        .order_by(ProducerEvent.created_at)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            handed = []
            for event in rows:
                if await still_enabled(event.extension_id):
                    event.handed_out_at = now
                    handed.append(event)
                else:
                    event.state = DROPPED
                    event.dropped_reason = EXTENSION_OFF
                    event.updated_at = now
            return handed

    async def ack(
        self,
        org_id: str,
        event_ids: list[str],
        *,
        not_handled: dict[str, str] | None = None,
        producer_kind: str | None = None,
        producer_id: str | None = None,
    ) -> int:
        """Mark pending items delivered — or, for the events in *not_handled* (id → why), not handled.

        With a producer, only that producer's. Counts both.
        """
        reasons = not_handled or {}
        now = datetime.now(timezone.utc)
        acked = 0
        async with self.session_scope(org_id) as session:
            for event_id, reason in reasons.items():
                acked += await self._settle(
                    session,
                    update(ProducerEvent).where(ProducerEvent.id == event_id, ProducerEvent.kind == 'event'),
                    {'state': NOT_HANDLED, 'not_handled_reason': reason, 'delivered_at': now, 'updated_at': now},
                    producer_kind=producer_kind,
                    producer_id=producer_id,
                )
            delivered = [event_id for event_id in event_ids if event_id not in reasons]
            if delivered:
                acked += await self._settle(
                    session,
                    update(ProducerEvent).where(ProducerEvent.id.in_(delivered)),
                    {'state': DELIVERED, 'delivered_at': now, 'updated_at': now},
                    producer_kind=producer_kind,
                    producer_id=producer_id,
                )
        return acked

    @staticmethod
    async def _settle(  # noqa: ANN205 — a SQLAlchemy update and the open AsyncSession
        session, query, values: dict[str, object], *, producer_kind: str | None, producer_id: str | None
    ) -> int:
        query = query.where(ProducerEvent.state == PENDING)
        if producer_kind is not None and producer_id is not None:
            query = query.where(ProducerEvent.producer_kind == producer_kind, ProducerEvent.producer_id == producer_id)
        result = await session.execute(query.values(**values))
        return int(result.rowcount or 0)

    async def fail(self, org_id: str, event_ids: list[str], reason: str) -> int:
        """The gateway gave up delivering these: pending ones become failed, never handed out again."""
        now = datetime.now(timezone.utc)
        async with self.session_scope(org_id) as session:
            result = await session.execute(
                update(ProducerEvent)
                .where(ProducerEvent.id.in_(event_ids), ProducerEvent.state == PENDING)
                .values(state=FAILED, failed_reason=reason, updated_at=now)
            )
            return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def drop_for(self, org_id: str, *, producer_kind: str, producer_ids: list[str], reason: str) -> int:
        """Drop the pending events of producers that are gone, saying why."""
        now = datetime.now(timezone.utc)
        dropped = 0
        async with self.session_scope(org_id) as session:
            for part in chunks(producer_ids):
                result = await session.execute(
                    update(ProducerEvent)
                    .where(
                        ProducerEvent.producer_kind == producer_kind,
                        ProducerEvent.producer_id.in_(part),
                        ProducerEvent.state == PENDING,
                    )
                    .values(state=DROPPED, dropped_reason=reason, updated_at=now)
                )
                dropped += int(result.rowcount or 0)  # type: ignore[attr-defined]
        return dropped

    async def expire_due(self, org_id: str) -> int:
        """Mark every pending event whose `expires_at` has passed as `expired`.

        Run before anything reads states, and by the retention round, so a
        service polling sees an expiry whether or not anybody else looked.
        """
        now = datetime.now(timezone.utc)
        async with self.session_scope(org_id) as session:
            result = await session.execute(
                update(ProducerEvent)
                .where(ProducerEvent.state == PENDING, ProducerEvent.expires_at <= now)
                .values(state=EXPIRED, updated_at=now)
            )
            return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def of_client(self, org_id: str, client_id: str, event_id: str) -> ProducerEvent | None:
        """One event, only if this service sent it."""
        async with self.session_scope(org_id) as session:
            event = await session.get(ProducerEvent, event_id)
        return event if event is not None and event.client_id == client_id else None

    async def sent_by(
        self, org_id: str, client_id: str, *, state: str | None, since: datetime | None, limit: int
    ) -> list[ProducerEvent]:
        """A service's own events, oldest change first: poll with `since` = the last `updated_at` seen."""
        query = select(ProducerEvent).where(ProducerEvent.client_id == client_id)
        if state is not None:
            query = query.where(ProducerEvent.state == state)
        if since is not None:
            query = query.where(ProducerEvent.updated_at > since)
        async with self.session_scope(org_id) as session:
            rows = await session.execute(query.order_by(ProducerEvent.updated_at, ProducerEvent.id).limit(limit))
            return list(rows.scalars().all())

    async def purge(self, org_id: str, *, older_than_days: int) -> int:
        """Delete events that left `pending` more than *older_than_days* ago."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        async with self.session_scope(org_id) as session:
            result = await session.execute(
                delete(ProducerEvent).where(ProducerEvent.state != PENDING, ProducerEvent.updated_at < cutoff)
            )
            return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def waiting(self, org_id: str) -> list[tuple[str, str]]:
        """Producers with a deliverable event: (kind, id)."""
        now = datetime.now(timezone.utc)
        async with self.session_scope(org_id) as session:
            rows = await session.execute(
                select(ProducerEvent.producer_kind, ProducerEvent.producer_id).where(self._deliverable(now)).distinct()
            )
            return [(str(kind), str(pid)) for kind, pid in rows.all()]
