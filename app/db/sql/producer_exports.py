"""Exports of removed members' data, in the organisation's own database (archives as large objects)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

from sqlalchemy import bindparam, delete, or_, select, text
from sqlalchemy.dialects.postgresql import BYTEA

from app.db.sql.models.producer_export import FAILED, HANDED_OUT, ProducerExport, READY, REQUESTED
from app.db.sql.tenancy import TenantData

#: An export handed to a node agent and not finished within this is handed out again.
LEASE = timedelta(minutes=30)

_CREATE = text('SELECT lo_create(0)')
_PUT = text('SELECT lo_put(:oid, :offset, :data)').bindparams(bindparam('data', type_=BYTEA))
_GET = text('SELECT lo_get(:oid, :offset, :length)')


class ArchiveTooLarge(Exception):
    """The upload passed the largest archive kept."""


class ProducerExportsDB(TenantData):
    async def add(self, org_id: str, export: ProducerExport) -> ProducerExport:
        async with self.session_scope(org_id) as session:
            session.add(export)
        return export

    async def get(self, org_id: str, export_id: str) -> ProducerExport | None:
        async with self.session_scope(org_id) as session:
            return await session.get(ProducerExport, export_id)

    async def all(self, org_id: str) -> list[ProducerExport]:
        async with self.session_scope(org_id) as session:
            rows = await session.execute(select(ProducerExport).order_by(ProducerExport.requested_at.desc()))
            return list(rows.scalars().all())

    async def pull(self, org_id: str, *, by_client_id: str) -> list[ProducerExport]:
        """Hand out what is waiting, and what a node agent took and never finished."""
        now = datetime.now(timezone.utc)
        async with self.session_scope(org_id) as session:
            rows = (
                (
                    await session.execute(
                        select(ProducerExport)
                        .where(
                            or_(
                                ProducerExport.state == REQUESTED,
                                (ProducerExport.state == HANDED_OUT) & (ProducerExport.handed_out_at < now - LEASE),
                            )
                        )
                        .order_by(ProducerExport.requested_at)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                row.state = HANDED_OUT
                row.handed_to = by_client_id
                row.handed_out_at = now
            return list(rows)

    async def fail(self, org_id: str, export_id: str, error: str) -> bool:
        async with self.session_scope(org_id) as session:
            row = await session.get(ProducerExport, export_id)
            if row is None or row.state not in (REQUESTED, HANDED_OUT):
                return False
            row.state = FAILED
            row.error = error
            row.finished_at = datetime.now(timezone.utc)
            return True

    async def store(
        self, org_id: str, export_id: str, chunks: AsyncIterator[bytes], *, max_bytes: int, keep_for: timedelta
    ) -> ProducerExport | None:
        """Write the archive and make the export ready; None unless it was handed out and waiting."""
        async with self.session_scope(org_id) as session:
            row = await session.get(ProducerExport, export_id, with_for_update=True)
            if row is None or row.state != HANDED_OUT:
                return None
            oid = int((await session.execute(_CREATE)).scalar_one())
            offset = 0
            async for chunk in chunks:
                if offset + len(chunk) > max_bytes:
                    raise ArchiveTooLarge(f'An export may be at most {max_bytes} bytes')
                await session.execute(_PUT, {'oid': oid, 'offset': offset, 'data': chunk})
                offset += len(chunk)
            now = datetime.now(timezone.utc)
            row.content_oid = oid
            row.size = offset
            row.state = READY
            row.finished_at = now
            row.expires_at = now + keep_for
            return row

    async def read(self, org_id: str, oid: int, offset: int, length: int) -> bytes:
        async with self.session_scope(org_id) as session:
            return bytes((await session.execute(_GET, {'oid': oid, 'offset': offset, 'length': length})).scalar_one())

    async def expire(self, org_id: str) -> int:
        """Delete ready archives past their time, and failed exports older than a day; triggers unlink the bytes."""
        now = datetime.now(timezone.utc)
        async with self.session_scope(org_id) as session:
            result = await session.execute(
                delete(ProducerExport).where(
                    or_(
                        (ProducerExport.state == READY) & (ProducerExport.expires_at < now),
                        (ProducerExport.state == FAILED) & (ProducerExport.finished_at < now - timedelta(days=1)),
                    )
                )
            )
            return int(result.rowcount or 0)  # type: ignore[attr-defined]
