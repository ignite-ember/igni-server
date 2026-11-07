"""Where each removed or discarded producer's erasure stands, discarding now, and the gateway's report of one done.

An org admin **discards** what is no longer wanted (`docs/service-identity.md`
§18.5):

* **an app** — removed at once (`OrgApps.discard`): its VM can never be
  woken again, its credential stops, its place under the licence is free
  at once, and its VM's memory, snapshots, volume and keys are erased in
  the background straight away;
* **a removed member's VM** — erased now instead of at the end of the
  organisation's retention. A current member's VM is never discarded.

A legal hold stops either erasure (the app is still gone). The gateway
reports each erasure done; the portal shows it, and both the discard and
the completion are rows in the organisation's audit trail.
"""

from __future__ import annotations

import builtins
from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.api.base_v1.schema.retention import DiscardedMember, ErasureDoneBody, ErasureList, ErasureRecord
from app.db.sql.audit_events import AuditEventDB, AuditRecord
from app.db.sql.models.producer_feed import PERSON, ProducerFeedEntry
from app.db.sql.producer_feed import ProducerFeedDB
from app.db.sql.retention import RetentionDB
from app.db.sql.team import TeamDB
from app.db.sql.user import UserDB
from app.security.org_authority import OrgAuthority
from app.security.producer import ProducerRef
from app.services.cloud.erasure_audit import ROUTE as ERASURE_ROUTE
from app.services.cloud.producer_exports import ProducerExports
from app.services.cloud.producer_feed import ProducerFeed

#: Exports that still count: a discard ends the chance to take another.
LIVE_EXPORTS = ('requested', 'handed_out', 'ready')


class Erasures:
    def __init__(self) -> None:
        self._feed = ProducerFeedDB()

    @staticmethod
    def state(row: ProducerFeedEntry, now: datetime | None = None) -> str:
        if row.erasure_done_at is not None:
            return 'erased'
        if row.erase_at is None:
            return 'held'
        return 'erasing' if row.erase_at <= (now or datetime.now(timezone.utc)) else 'scheduled'

    async def _names(self, rows: builtins.list[ProducerFeedEntry]) -> dict[str, str]:
        names: dict[str, str] = {}
        for row in rows:
            if row.producer_kind == PERSON and row.producer_id not in names:
                user = await UserDB().get_by_user_id(row.producer_id)
                if user is not None and user.email:
                    names[row.producer_id] = str(user.email)
        return names

    async def _exports(self, org_id: str) -> dict[tuple[str, str], int]:
        counts: dict[tuple[str, str], int] = {}
        for export in (await ProducerExports().list(org_id)).exports:
            if export.state in LIVE_EXPORTS:
                key = (export.producer.id, export.incarnation)
                counts[key] = counts.get(key, 0) + 1
        return counts

    def record(
        self, row: ProducerFeedEntry, names: dict[str, str], exports: dict[tuple[str, str], int]
    ) -> ErasureRecord:
        return ErasureRecord(
            producer=ProducerRef(kind=row.producer_kind, id=row.producer_id),  # type: ignore[arg-type]
            name=row.discarded_label or names.get(row.producer_id),
            incarnation=row.incarnation,
            removed_at=row.removed_at,
            erase_at=row.erase_at,
            state=self.state(row),  # type: ignore[arg-type]
            erased_at=row.erasure_done_at,
            legal_hold=row.erase_at is None and row.erasure_done_at is None,
            exports=exports.get((row.producer_id, row.incarnation), 0),
            discarded_by_user_id=row.discarded_by_user_id,
            discarded_at=row.discarded_at,
        )

    async def _records(self, org_id: str, rows: builtins.list[ProducerFeedEntry]) -> builtins.list[ErasureRecord]:
        names = await self._names(rows)
        exports = await self._exports(org_id)
        return [self.record(row, names, exports) for row in rows]

    async def list(self, org_id: str) -> ErasureList:
        await ProducerFeed().drain()
        return ErasureList(org_id=org_id, erasures=await self._records(org_id, await self._feed.removed_in(org_id)))

    async def of(self, org_id: str, kind: str, producer_id: str) -> builtins.list[ErasureRecord]:
        rows = [
            row
            for row in await self._feed.removed_in(org_id)
            if (row.producer_kind, row.producer_id) == (kind, producer_id)
        ]
        return await self._records(org_id, rows)

    async def discard_member(self, authority: OrgAuthority, user_id: str, incarnation: str | None) -> DiscardedMember:
        """A removed member's VM erased now, instead of after the retention. Never a current member's."""
        org_id = authority.org_id
        hold = await RetentionDB().hold(org_id, user_id)
        if hold is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f'A legal hold stands on this person here (placed {hold.placed_at:%Y-%m-%d}: {hold.reason}); '
                    'nothing of theirs is erased until it is released'
                ),
            )
        await ProducerFeed().drain()
        rows = [
            row
            for row in await self._feed.removed_in(org_id)
            if row.producer_kind == PERSON
            and row.producer_id == user_id
            and incarnation in (None, row.incarnation)
            and row.erasure_done_at is None
        ]
        if not rows:
            member = await TeamDB().get_member(org_id, user_id)
            if member is not None and member.status in ('active', 'deactivated'):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A current member's VM is never discarded: it is kept while they are a member",
                )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='No removed VM of this person here is waiting for its erasure',
            )
        await ProducerFeed().discard_person(org_id, user_id, incarnation, by_user_id=authority.user_id)
        return DiscardedMember(erasures=await self.of(org_id, PERSON, user_id))

    async def done(
        self, org_id: str, producer: ProducerRef, body: ErasureDoneBody, *, by_client_id: str
    ) -> ErasureRecord:
        """The gateway erased a removed incarnation's VM: recorded once, audited with what was erased."""
        row, first = await ProducerFeed().erasure_done(org_id, producer.kind, producer.id, body.incarnation)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No such removed incarnation')
        if first:
            await AuditEventDB().record(
                AuditRecord(
                    method='SYSTEM',
                    route=ERASURE_ROUTE,
                    status_code=200,
                    org_id=org_id,
                    target_type=f'{producer.kind}_vm',
                    target_id=producer.id,
                    detail={
                        'incarnation': body.incarnation,
                        'erase_at': row.erase_at.isoformat() if row.erase_at else None,
                        'why': 'erased',
                        'by_client': by_client_id,
                        'summary': body.summary,
                    },
                )
            )
        [record] = await self._records(org_id, [row])
        return record
