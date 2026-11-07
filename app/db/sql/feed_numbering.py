"""Numbering the marked producers whose state differs from what the feed last reported.

See `app.db.sql.models.producer_feed` for why `seq` is safe to page by:
numbers are drawn only under the feed's lock, one transaction at a time.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, insert, select

from app.db.sql.bind_chunks import chunks
from app.db.sql.feed_transaction import FeedTransaction
from app.db.sql.models.producer_feed import ProducerFeedEntry, ProducerFeedMark
from app.db.sql.schemas.producer_feed import Census, FeedChanges, FeedMark, ProducerState, Scheduled


class Change(BaseModel):
    """A stored row to number again: given a new state, made inactive, or removed."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    row: ProducerFeedEntry
    #: What it is now; None when it is inactive or removed.
    state: ProducerState | None = None
    removed: bool = False

    @property
    def stops(self) -> bool:
        """An active producer made inactive or removed: its tokens stop."""
        return self.row.active and (self.removed or self.state is None or not self.state.active)


class FeedNumbering(FeedTransaction):
    """The drain's transaction: the marks read and forgotten, and what differs numbered."""

    async def marks(self) -> list[FeedMark]:
        rows = await self._session.execute(select(ProducerFeedMark))
        return [
            FeedMark(org_id=m.org_id, producer_kind=m.producer_kind, producer_id=m.producer_id, n=m.n)
            for m in rows.scalars().all()
        ]

    async def forget(self, marks: list[FeedMark]) -> None:
        """Delete the marks read, unless marked again since — those are numbered next time."""
        for mark in marks:
            await self._session.execute(
                delete(ProducerFeedMark).where(
                    ProducerFeedMark.org_id == mark.org_id,
                    ProducerFeedMark.producer_kind == mark.producer_kind,
                    ProducerFeedMark.producer_id == mark.producer_id,
                    ProducerFeedMark.n == mark.n,
                )
            )

    async def live_ids(self, org_id: str, kind: str) -> list[str]:
        """Producers of this kind the feed has in the organisation and has not reported removed."""
        rows = await self._session.execute(
            select(ProducerFeedEntry.producer_id).where(
                ProducerFeedEntry.org_id == org_id,
                ProducerFeedEntry.producer_kind == kind,
                ProducerFeedEntry.removed.is_(False),
            )
        )
        return [str(producer_id) for producer_id in rows.scalars().all()]

    async def record(self, org_id: str, kind: str, ids: list[str], census: Census) -> FeedChanges:
        """Number what differs among these producers.

        A removed row is never changed again. A producer whose
        incarnation changed — a person removed and added again — gets a
        new row: the live one, if any, is removed, and a removed one is
        never brought back.
        """
        current = {state.key.producer_id: state for state in census.states}
        stored = await self._rows(org_id, kind, ids)
        fresh: list[ProducerState] = []
        changed: list[Change] = []
        for pid in ids:
            rows, state = stored.get(pid, []), current.get(pid)
            live = next((row for row in rows if not row.removed), None)
            if state is not None:
                if live is not None and live.incarnation != state.incarnation:
                    changed.append(Change(row=live, removed=True))
                    live = None
                if live is None:
                    if all(row.incarnation != state.incarnation for row in rows):
                        fresh.append(state)
                elif self._differs(live, state):
                    changed.append(Change(row=live, state=state))
            elif live is not None and pid in census.removed:
                changed.append(Change(row=live, removed=True))
            elif live is not None and live.active:
                changed.append(Change(row=live))
        count = len(fresh) + len(changed)
        if count == 0:
            return FeedChanges()
        numbers = iter(await self._numbers(count))
        now = datetime.now(timezone.utc)
        scheduled: list[Scheduled] = []
        # What changed first: an old incarnation's removal is numbered before
        # the new incarnation that replaces it.
        for change in changed:
            row = change.row
            if change.stops:
                self._revoke(row, 'removed' if change.removed else 'inactive')
            if change.state is not None:
                self._update(row, change.state)
            else:
                row.active = False
                if change.removed:
                    scheduled.append(self._remove(row, census, now))
            row.changed_at = now
            row.seen_removed_at = None
            row.seq = next(numbers)
        if fresh:
            await self._insert(org_id, kind, fresh, numbers, now)
        await self._session.flush()
        return FeedChanges(numbered=count, scheduled=scheduled)

    async def _rows(self, org_id: str, kind: str, ids: list[str]) -> dict[str, list[ProducerFeedEntry]]:
        """Every row of each of these producers: one per incarnation."""
        found: dict[str, list[ProducerFeedEntry]] = {}
        for part in chunks(ids):
            rows = await self._session.execute(
                select(ProducerFeedEntry).where(
                    ProducerFeedEntry.org_id == org_id,
                    ProducerFeedEntry.producer_kind == kind,
                    ProducerFeedEntry.producer_id.in_(part),
                )
            )
            for row in rows.scalars().all():
                found.setdefault(row.producer_id, []).append(row)
        return found

    @staticmethod
    def _differs(row: ProducerFeedEntry, state: ProducerState) -> bool:
        """The profile itself is compared by its version, which changes exactly when it does."""
        return row.vm_profile is None or (
            row.active,
            row.profile_version,
            row.packages_version,
            row.volume_gib,
            row.legal_hold,
        ) != (
            state.active,
            state.profile_version,
            state.packages_version,
            state.volume_gib,
            state.legal_hold,
        )

    @staticmethod
    def _update(row: ProducerFeedEntry, state: ProducerState) -> None:
        row.active = state.active
        row.profile_version = state.profile_version
        row.packages_version = state.packages_version
        row.vm_profile = state.profile.model_dump(mode='json')
        row.volume_gib = state.volume_gib
        row.legal_hold = state.legal_hold

    def _remove(self, row: ProducerFeedEntry, census: Census, now: datetime) -> Scheduled:
        """Removed now: its erasure scheduled after its retention, or not at all while held."""
        days = census.erase_after_days.get(row.producer_id, 0)
        held = row.producer_id in census.held or (row.owner_app_id or '') in census.held_apps
        row.removed = True
        row.removed_at = now
        row.erase_after_days = days
        row.erase_at = None if held else now + timedelta(days=days)
        return self._scheduled(row, 'held' if held else 'removed')

    async def _insert(
        self, org_id: str, kind: str, fresh: list[ProducerState], numbers: Iterator[int], now: datetime
    ) -> None:
        """A row for each producer, or incarnation, the feed has never listed."""
        await self._session.execute(
            insert(ProducerFeedEntry),
            [
                {
                    'org_id': org_id,
                    'producer_kind': kind,
                    'producer_id': state.key.producer_id,
                    'active': state.active,
                    'profile_version': state.profile_version,
                    'packages_version': state.packages_version,
                    'vm_profile': state.profile.model_dump(mode='json'),
                    'volume_gib': state.volume_gib,
                    'owner_app_id': state.owner_app_id,
                    'incarnation': state.incarnation,
                    'changed_at': now,
                    'seq': next(numbers),
                    'removed': False,
                    'erased': False,
                    'legal_hold': state.legal_hold,
                }
                for state in fresh
            ],
        )
