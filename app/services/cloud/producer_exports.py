"""Exports of a removed member's data, during retention (`docs/service-identity.md` §18.4).

While a removed incarnation of a person waits for its erasure — or is
held — an org admin may ask for an export of what igni holds of it: the
conversations and the rest of the producer's data in the organisation's
igni database. igni server queues it; the node agent pulls it, runs
`igni-cloud db export-producer --org <org_id> --producer person:<id> --incarnation <id> --out <file>`
against that database, and uploads the archive; the org admin downloads
it until it expires (`PRODUCER_EXPORT_RETENTION_DAYS`, default 7). An
incarnation already erased cannot be exported. The workspace volume is
not part of it yet.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
import uuid

from fastapi import HTTPException, status

from app.api.base_v1.schema.retention import (
    ExportList,
    ExportRecord,
    PendingExport,
    PendingExports,
)
from app.config import settings
from app.db.sql.models.producer_export import ProducerExport, READY
from app.db.sql.producer_exports import ArchiveTooLarge, ProducerExportsDB
from app.db.sql.producer_feed import ProducerFeedDB
from app.db.sql.tenancy import TenantData
from app.security.org_authority import OrgAuthority
from app.security.producer import ProducerRef
from app.services.auth.jwt_service import PERSON

#: Bytes per read of an archive being downloaded.
CHUNK = 1024 * 1024


class ProducerExports:
    def __init__(self) -> None:
        self._db = ProducerExportsDB()

    @staticmethod
    def record(org_id: str, row: ProducerExport) -> ExportRecord:
        return ExportRecord(
            export_id=row.id,
            org_id=org_id,
            producer=ProducerRef(kind=row.producer_kind, id=row.producer_id),  # type: ignore[arg-type]
            incarnation=row.incarnation,
            state=row.state,
            requested_by_user_id=row.requested_by_user_id,
            requested_at=row.requested_at,
            handed_out_at=row.handed_out_at,
            finished_at=row.finished_at,
            size=row.size,
            error=row.error,
            expires_at=row.expires_at,
        )

    @staticmethod
    def command(org_id: str, row: ProducerExport) -> list[str]:
        """igni's export, for the node agent to run with `--out <file>` appended."""
        return [
            'igni-cloud',
            'db',
            'export-producer',
            '--org',
            org_id,
            '--producer',
            f'{row.producer_kind}:{row.producer_id}',
            '--incarnation',
            row.incarnation,
        ]

    @staticmethod
    async def _waiting(org_id: str, user_id: str) -> list[str]:
        """The person's removed incarnations here not yet erased, oldest first."""
        return [row.incarnation for row in await ProducerFeedDB().removed_incarnations(org_id, user_id)]

    async def request(self, authority: OrgAuthority, user_id: str, incarnation: str | None) -> ExportRecord:
        waiting = await self._waiting(authority.org_id, user_id)
        if not waiting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='This person has no removed incarnation here waiting for erasure',
            )
        chosen = incarnation or waiting[-1]
        if chosen not in waiting:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail='That incarnation is not waiting for erasure: it was erased, or is not removed',
            )
        row = await self._db.add(
            authority.org_id,
            ProducerExport(
                id=str(uuid.uuid4()),
                producer_kind=PERSON,
                producer_id=user_id,
                incarnation=chosen,
                requested_by_user_id=authority.user_id,
            ),
        )
        return self.record(authority.org_id, row)

    async def list(self, org_id: str) -> ExportList:
        return ExportList(exports=[self.record(org_id, row) for row in await self._db.all(org_id)])

    async def _found(self, org_id: str, export_id: str) -> ProducerExport:
        row = await self._db.get(org_id, export_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No such export')
        return row

    async def get(self, org_id: str, export_id: str) -> ExportRecord:
        return self.record(org_id, await self._found(org_id, export_id))

    async def archive(self, org_id: str, export_id: str) -> tuple[ProducerExport, AsyncIterator[bytes]]:
        row = await self._found(org_id, export_id)
        if row.state != READY or row.content_oid is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f'The export is {row.state}, not ready')
        oid, size = int(row.content_oid), int(row.size or 0)

        async def chunks() -> AsyncIterator[bytes]:
            offset = 0
            while offset < size:
                data = await self._db.read(org_id, oid, offset, min(CHUNK, size - offset))
                if not data:
                    return
                offset += len(data)
                yield data

        return row, chunks()

    # ── The node agent ──────────────────────────────────────────────

    async def pending(self, *, by_client_id: str) -> PendingExports:
        """Hand out waiting exports; one whose incarnation was erased meanwhile fails instead."""
        handed: list[PendingExport] = []
        for org_id in await TenantData().each_tenant():
            for row in await self._db.pull(org_id, by_client_id=by_client_id):
                if row.incarnation not in await self._waiting(org_id, row.producer_id):
                    await self._db.fail(org_id, row.id, 'the incarnation was erased before it could be exported')
                    continue
                handed.append(
                    PendingExport(
                        export_id=row.id,
                        org_id=org_id,
                        producer=ProducerRef(kind=row.producer_kind, id=row.producer_id),  # type: ignore[arg-type]
                        incarnation=row.incarnation,
                        command=self.command(org_id, row),
                    )
                )
        return PendingExports(exports=handed)

    async def upload(self, org_id: str, export_id: str, chunks: AsyncIterator[bytes]) -> ExportRecord:
        try:
            row = await self._db.store(
                org_id,
                export_id,
                chunks,
                max_bytes=settings.producer_export_max_bytes,
                keep_for=timedelta(days=settings.producer_export_retention_days),
            )
        except ArchiveTooLarge as exc:
            await self._db.fail(org_id, export_id, str(exc))
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)) from exc
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail='This export is not handed out and waiting'
            )
        return self.record(org_id, row)

    async def failed(self, org_id: str, export_id: str, error: str) -> None:
        if not await self._db.fail(org_id, export_id, error):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='This export is not waiting')

    async def close_out(self) -> int:
        """The retention round: delete expired archives."""
        return sum([await self._db.expire(org_id) for org_id in await TenantData().each_tenant()])
