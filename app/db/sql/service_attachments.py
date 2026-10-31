"""Files services uploaded for the items they send, in the organisation's own database."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import defer

from app.db.sql.models.producer_event import DELIVERED, PENDING, ProducerEvent
from app.db.sql.models.service_attachment import ServiceAttachment
from app.db.sql.tenancy import TenantData


class ServiceAttachmentsDB(TenantData):
    async def put(self, org_id: str, attachment: ServiceAttachment) -> ServiceAttachment:
        async with self.session_scope(org_id) as session:
            session.add(attachment)
        return attachment

    async def described(self, org_id: str, ids: list[str]) -> dict[str, ServiceAttachment]:
        """The attachments by id, described: their bytes are not loaded."""
        if not ids:
            return {}
        async with self.session_scope(org_id) as session:
            rows = await session.execute(
                select(ServiceAttachment).options(defer(ServiceAttachment.content)).where(ServiceAttachment.id.in_(ids))
            )
            return {row.id: row for row in rows.scalars().all()}

    async def for_producer(
        self, org_id: str, *, event_id: str, attachment_id: str, producer_kind: str, producer_id: str
    ) -> ServiceAttachment | None:
        """One attachment of one item, only for the producer the item is for, while it is unexpired.

        Pending or delivered: igni fetches before it acknowledges, but the
        other side of a lease (the gateway, or the desktop) may have
        acknowledged the item meanwhile, and its files must still be there.
        """
        now = datetime.now(timezone.utc)
        async with self.session_scope(org_id) as session:
            row = await session.execute(
                select(ServiceAttachment)
                .join(ProducerEvent, ProducerEvent.id == ServiceAttachment.event_id)
                .where(
                    ServiceAttachment.id == attachment_id,
                    ServiceAttachment.event_id == event_id,
                    ServiceAttachment.expires_at > now,
                    ProducerEvent.producer_kind == producer_kind,
                    ProducerEvent.producer_id == producer_id,
                    ProducerEvent.state.in_([PENDING, DELIVERED]),
                )
            )
            return row.scalars().first()

    async def purge_expired(self, org_id: str) -> int:
        """Delete every attachment past its expiry — named by an item or never named."""
        now = datetime.now(timezone.utc)
        async with self.session_scope(org_id) as session:
            result = await session.execute(delete(ServiceAttachment).where(ServiceAttachment.expires_at <= now))
            return int(result.rowcount or 0)  # type: ignore[attr-defined]
