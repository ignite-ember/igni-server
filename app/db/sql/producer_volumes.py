"""Producers' volumes grown past their profile's size, in the organisation's own database."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.sql.bind_chunks import chunks
from app.db.sql.models.producer_volume import ProducerVolumeSize
from app.db.sql.tenancy import TenantData


class ProducerVolumesDB(TenantData):
    async def get(self, org_id: str, producer_kind: str, producer_id: str) -> ProducerVolumeSize | None:
        async with self.session_scope(org_id) as session:
            return await session.get(ProducerVolumeSize, (producer_kind, producer_id))

    async def of(self, org_id: str, producer_kind: str, producer_ids: list[str]) -> dict[str, ProducerVolumeSize]:
        found: dict[str, ProducerVolumeSize] = {}
        async with self.session_scope(org_id) as session:
            for part in chunks(producer_ids):
                rows = await session.execute(
                    select(ProducerVolumeSize).where(
                        ProducerVolumeSize.producer_kind == producer_kind, ProducerVolumeSize.producer_id.in_(part)
                    )
                )
                found.update({row.producer_id: row for row in rows.scalars().all()})
        return found

    async def put(self, org_id: str, size: ProducerVolumeSize) -> None:
        """Record the size: one row per producer, the latest incarnation's."""
        values = {
            'producer_kind': size.producer_kind,
            'producer_id': size.producer_id,
            'incarnation': size.incarnation,
            'gib': size.gib,
            'grown_by_kind': size.grown_by_kind,
            'grown_by_id': size.grown_by_id,
            'grown_as': size.grown_as,
            'grown_at': datetime.now(timezone.utc),
        }
        async with self.session_scope(org_id) as session:
            await session.execute(
                insert(ProducerVolumeSize)
                .values(**values)
                .on_conflict_do_update(constraint='pk_producer_volumes', set_=values)
            )
