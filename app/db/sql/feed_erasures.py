"""Erasures moved under the feed's lock: legal holds, discards, erasure on request, and erasure done."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, or_, select, update

from app.db.sql.feed_transaction import FeedTransaction
from app.db.sql.models.app_space import SPACE
from app.db.sql.models.producer_feed import PERSON, ProducerFeedEntry
from app.db.sql.models.registered_client import APP
from app.db.sql.schemas.producer_feed import Scheduled


class FeedErasures(FeedTransaction):
    """When removed producers are erased, changed in a transaction that numbers what it moves."""

    async def reschedule(self, org_id: str, user_id: str, *, held: bool) -> list[Scheduled]:
        """A legal hold placed (no erasure) or released (erasure after the retention, or now if it has run out).

        An incarnation already erased — its `erase_at` passed — is past
        holding, and is left as it is. The live incarnation, if any, is
        listed again with `legal_hold`, so the gateway stops (or resumes)
        erasing its VM after inactivity (§12.3) at once.
        """
        return await self._hold(await self._person_rows(user_id, org_id), held=held, released=self._after_retention)

    async def reschedule_app(self, org_id: str, app_id: str, *, held: bool) -> list[Scheduled]:
        """A legal hold placed on an app or released: the app's rows and its spaces'.

        Live rows carry `legal_hold`, so the gateway stops (or resumes)
        erasing them after inactivity. A removed row not yet due is held
        (no erasure) or, released, erased now: an app or a space is erased
        at once when removed. One whose `erase_at` has passed is past holding.
        """
        rows = await self._session.execute(
            select(ProducerFeedEntry).where(
                ProducerFeedEntry.org_id == org_id,
                or_(
                    and_(ProducerFeedEntry.producer_kind == APP, ProducerFeedEntry.producer_id == app_id),
                    and_(ProducerFeedEntry.producer_kind == SPACE, ProducerFeedEntry.owner_app_id == app_id),
                ),
            )
        )
        return await self._hold(list(rows.scalars().all()), held=held, released=self._at_once, mark_removed=True)

    async def _hold(
        self,
        rows: list[ProducerFeedEntry],
        *,
        held: bool,
        released: Callable[[ProducerFeedEntry, datetime], datetime],
        mark_removed: bool = False,
    ) -> list[Scheduled]:
        """A hold placed on these rows or released: live ones relisted, removed ones not yet due moved.

        A removed row moved carries `legal_hold` only when `mark_removed`
        (an app's or a space's); a removed person's keeps its own.
        """
        now = datetime.now(timezone.utc)
        moved: list[ProducerFeedEntry] = []
        relisted: list[ProducerFeedEntry] = []
        for row in rows:
            if not row.removed:
                if row.legal_hold != held:
                    row.legal_hold = held
                    relisted.append(row)
                continue
            if row.erase_at is not None and row.erase_at <= now:
                continue
            erase_at = None if held else released(row, now)
            if erase_at != row.erase_at:
                row.erase_at = erase_at
                if mark_removed:
                    row.legal_hold = held
                moved.append(row)
        await self._renumber([*moved, *relisted])
        await self._session.flush()
        return [self._scheduled(row, 'held' if held else 'released') for row in moved]

    @staticmethod
    def _after_retention(row: ProducerFeedEntry, now: datetime) -> datetime:
        """A released person's erasure: after the retention they were removed under, or now if it has run out."""
        return max((row.removed_at or now) + timedelta(days=row.erase_after_days or 0), now)

    @staticmethod
    def _at_once(row: ProducerFeedEntry, now: datetime) -> datetime:
        """A released app's or space's erasure: now, as when it was removed."""
        return now

    async def _person_rows(self, user_id: str, org_id: str | None = None) -> list[ProducerFeedEntry]:
        query = select(ProducerFeedEntry).where(
            ProducerFeedEntry.producer_kind == PERSON, ProducerFeedEntry.producer_id == user_id
        )
        if org_id is not None:
            query = query.where(ProducerFeedEntry.org_id == org_id)
        return list((await self._session.execute(query)).scalars().all())

    async def discard_person(
        self, org_id: str, user_id: str, incarnation: str | None, *, by_user_id: str
    ) -> list[Scheduled]:
        """A removed member's incarnations (or the one named) erased now instead of after the retention.

        The caller has made sure no legal hold stands. One already due is
        left as it is.
        """
        now = datetime.now(timezone.utc)
        moved: list[ProducerFeedEntry] = []
        for row in await self._person_rows(user_id, org_id):
            if not row.removed or row.erasure_done_at is not None or incarnation not in (None, row.incarnation):
                continue
            if row.erase_at is not None and row.erase_at <= now:
                continue
            row.erase_at = now
            row.discarded_by_user_id = by_user_id
            row.discarded_at = now
            moved.append(row)
        await self._renumber(moved)
        await self._session.flush()
        return [self._scheduled(row, 'discarded') for row in moved]

    async def label_discarded(self, org_id: str, kind: str, producer_id: str, *, label: str, by_user_id: str) -> None:
        """An app discarded on the portal: its removed row keeps its name and who discarded it."""
        await self._session.execute(
            update(ProducerFeedEntry)
            .where(
                ProducerFeedEntry.org_id == org_id,
                ProducerFeedEntry.producer_kind == kind,
                ProducerFeedEntry.producer_id == producer_id,
                ProducerFeedEntry.removed.is_(True),
                ProducerFeedEntry.discarded_at.is_(None),
            )
            .values(discarded_label=label, discarded_by_user_id=by_user_id, discarded_at=datetime.now(timezone.utc))
        )

    async def erasure_state(self, org_id: str, kind: str, producer_id: str, incarnation: str) -> datetime | None:
        """When this removed incarnation's erasure was reported done; None if not (or no such row)."""
        return (
            await self._session.execute(
                select(ProducerFeedEntry.erasure_done_at).where(
                    ProducerFeedEntry.org_id == org_id,
                    ProducerFeedEntry.producer_kind == kind,
                    ProducerFeedEntry.producer_id == producer_id,
                    ProducerFeedEntry.incarnation == incarnation,
                    ProducerFeedEntry.removed.is_(True),
                )
            )
        ).scalar_one_or_none()

    async def erasure_done(
        self, org_id: str, kind: str, producer_id: str, incarnation: str
    ) -> ProducerFeedEntry | None:
        """The gateway erased this removed incarnation: when, for the portal and the trail. Idempotent."""
        row = (
            await self._session.execute(
                select(ProducerFeedEntry).where(
                    ProducerFeedEntry.org_id == org_id,
                    ProducerFeedEntry.producer_kind == kind,
                    ProducerFeedEntry.producer_id == producer_id,
                    ProducerFeedEntry.incarnation == incarnation,
                    ProducerFeedEntry.removed.is_(True),
                )
            )
        ).scalar_one_or_none()
        if row is not None and row.erasure_done_at is None:
            row.erasure_done_at = datetime.now(timezone.utc)
            await self._session.flush()
        return row

    async def erase_person(self, user_id: str) -> list[Scheduled]:
        """A person erased on request: every incarnation of theirs removed and erased now.

        Overrides the retention; the caller has made sure no legal hold
        stands. A row already erased and handed over goes at once.
        """
        now = datetime.now(timezone.utc)
        found = await self._person_rows(user_id)
        await self._session.execute(
            delete(ProducerFeedEntry).where(
                ProducerFeedEntry.producer_kind == PERSON,
                ProducerFeedEntry.producer_id == user_id,
                ProducerFeedEntry.erase_at <= now,
                ProducerFeedEntry.seen_removed_at.is_not(None),
            )
        )
        gone = {row.seq for row in found if row.erase_at is not None and row.erase_at <= now and row.seen_removed_at}
        moved: list[ProducerFeedEntry] = []
        for row in found:
            if row.seq in gone:
                continue
            row.erased = True
            if row.removed and row.erase_at is not None and row.erase_at <= now:
                continue
            if not row.removed:
                self._revoke(row, 'erased')
                row.removed = True
                row.active = False
                row.removed_at = now
            row.erase_after_days = 0
            row.erase_at = now
            moved.append(row)
        await self._renumber(moved)
        await self._session.flush()
        return [self._scheduled(row, 'erased_on_request') for row in moved]
