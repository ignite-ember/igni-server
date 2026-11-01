"""The gateway's feed of producers, on the shared plane: marks, numbering, paging, retention.

See `app.db.sql.models.producer_feed` for why `seq` is safe to page by:
numbers are drawn only inside `FeedNumbering`, one transaction at a time.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import math

from sqlalchemy import and_, CompoundSelect, delete, func, insert, or_, select, text, union, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.app_space import SPACE
from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.models.organization import Organization
from app.db.sql.models.producer_feed import ProducerFeedEntry, ProducerFeedMark
from app.db.sql.models.registered_client import APP, APPROVED, RegisteredClient
from app.db.sql.models.user import User
from app.db.sql.schemas.producer_feed import Census, FeedMark, Member, ProducerState, RevocationEntry, Scheduled
from app.utils.singleton import SingletonMeta

#: Held by the one transaction that numbers the feed, until it commits.
FEED_LOCK = 0x50524F4446454544

#: Ids per statement, well under the driver's limit on bind parameters.
_CHUNK = 5000

PERSON = 'person'


def chunks(values: Sequence[str]) -> Iterable[Sequence[str]]:
    """*values* in slices small enough for one statement's parameters."""
    for start in range(0, len(values), _CHUNK):
        yield values[start : start + _CHUNK]


class FeedNumbering:
    """The one transaction allowed to number the feed: it holds the lock until it commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        #: Erasures scheduled, moved or stopped in this transaction, for the audit trail.
        self.scheduled: list[Scheduled] = []
        #: Producers made inactive or removed here: their tokens stop (§21).
        self.revoked: list[RevocationEntry] = []

    @property
    def session(self) -> AsyncSession:
        """The locked transaction, for what must be numbered in it too."""
        return self._session

    def _revoke(self, row: ProducerFeedEntry, reason: str) -> None:
        self.revoked.append(
            RevocationEntry(
                kind=row.producer_kind,
                org_id=row.org_id,
                subject_id=row.producer_id,
                incarnation=row.incarnation,
                not_before_ms=math.ceil(datetime.now(timezone.utc).timestamp() * 1000),
                reason=reason,
            )
        )

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

    async def _numbers(self, count: int) -> list[int]:
        rows = await self._session.execute(
            select(func.nextval('producer_feed_seq')).select_from(func.generate_series(1, count))
        )
        return sorted(int(n) for n in rows.scalars().all())

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

    async def record(self, org_id: str, kind: str, ids: list[str], census: Census) -> int:
        """Number what differs among these producers.

        A removed row is never changed again. A producer whose
        incarnation changed — a person removed and added again — gets a
        new row: the live one, if any, is removed, and a removed one is
        never brought back.
        """
        current = {state.key.producer_id: state for state in census.states}
        stored = await self._rows(org_id, kind, ids)
        fresh: list[ProducerState] = []
        changed: list[tuple[ProducerFeedEntry, ProducerState | None, bool]] = []
        for pid in ids:
            rows, state = stored.get(pid, []), current.get(pid)
            live = next((row for row in rows if not row.removed), None)
            if state is not None:
                if live is not None and live.incarnation != state.incarnation:
                    changed.append((live, None, True))
                    live = None
                if live is None:
                    if all(row.incarnation != state.incarnation for row in rows):
                        fresh.append(state)
                elif self._differs(live, state):
                    changed.append((live, state, False))
            elif live is not None and pid in census.removed:
                changed.append((live, None, True))
            elif live is not None and live.active:
                changed.append((live, None, False))
        count = len(fresh) + len(changed)
        if count == 0:
            return 0
        numbers = iter(await self._numbers(count))
        now = datetime.now(timezone.utc)
        # What changed first: an old incarnation's removal is numbered before
        # the new incarnation that replaces it.
        for row, state, removed in changed:
            if row.active and (removed or state is None or not state.active):
                self._revoke(row, 'removed' if removed else 'inactive')
            if state is not None:
                row.active = state.active
                row.profile_version = state.profile_version
                row.packages_version = state.packages_version
                row.vm_profile = state.profile.model_dump(mode='json')
                row.volume_gib = state.volume_gib
                row.legal_hold = state.legal_hold
            else:
                row.active = False
                if removed:
                    self._remove(row, census, now)
            row.changed_at = now
            row.seen_removed_at = None
            row.seq = next(numbers)
        if fresh:
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
        await self._session.flush()
        return count

    def _remove(self, row: ProducerFeedEntry, census: Census, now: datetime) -> None:
        """Removed now: its erasure scheduled after its retention, or not at all while held."""
        days = census.erase_after_days.get(row.producer_id, 0)
        held = row.producer_id in census.held or (row.owner_app_id or '') in census.held_apps
        row.removed = True
        row.removed_at = now
        row.erase_after_days = days
        row.erase_at = None if held else now + timedelta(days=days)
        self.scheduled.append(self._scheduled(row, 'held' if held else 'removed'))

    @staticmethod
    def _scheduled(row: ProducerFeedEntry, why: str) -> Scheduled:
        return Scheduled(
            org_id=row.org_id,
            producer_kind=row.producer_kind,
            producer_id=row.producer_id,
            incarnation=row.incarnation,
            erase_at=row.erase_at,
            why=why,
        )

    async def _renumber(self, rows: list[ProducerFeedEntry]) -> None:
        numbers = iter(await self._numbers(len(rows))) if rows else iter([])
        now = datetime.now(timezone.utc)
        for row in rows:
            row.changed_at = now
            row.seen_removed_at = None
            row.seq = next(numbers)

    async def _person_rows(self, user_id: str, org_id: str | None = None) -> list[ProducerFeedEntry]:
        query = select(ProducerFeedEntry).where(
            ProducerFeedEntry.producer_kind == PERSON, ProducerFeedEntry.producer_id == user_id
        )
        if org_id is not None:
            query = query.where(ProducerFeedEntry.org_id == org_id)
        return list((await self._session.execute(query)).scalars().all())

    async def reschedule(self, org_id: str, user_id: str, *, held: bool) -> list[Scheduled]:
        """A legal hold placed (no erasure) or released (erasure after the retention, or now if it has run out).

        An incarnation already erased — its `erase_at` passed — is past
        holding, and is left as it is. The live incarnation, if any, is
        listed again with `legal_hold`, so the gateway stops (or resumes)
        erasing its VM after inactivity (§12.3) at once.
        """
        now = datetime.now(timezone.utc)
        moved: list[ProducerFeedEntry] = []
        relisted: list[ProducerFeedEntry] = []
        for row in await self._person_rows(user_id, org_id):
            if not row.removed:
                if row.legal_hold != held:
                    row.legal_hold = held
                    relisted.append(row)
                continue
            if row.erase_at is not None and row.erase_at <= now:
                continue
            if held:
                erase_at = None
            else:
                due = (row.removed_at or now) + timedelta(days=row.erase_after_days or 0)
                erase_at = max(due, now)
            if erase_at != row.erase_at:
                row.erase_at = erase_at
                moved.append(row)
        await self._renumber([*moved, *relisted])
        await self._session.flush()
        return [self._scheduled(row, 'held' if held else 'released') for row in moved]

    async def reschedule_app(self, org_id: str, app_id: str, *, held: bool) -> list[Scheduled]:
        """A legal hold placed on an app or released: the app's rows and its spaces'.

        Live rows carry `legal_hold`, so the gateway stops (or resumes)
        erasing them after inactivity. A removed row not yet due is held
        (no erasure) or, released, erased now: an app or a space is erased
        at once when removed. One whose `erase_at` has passed is past holding.
        """
        now = datetime.now(timezone.utc)
        rows = (
            (
                await self._session.execute(
                    select(ProducerFeedEntry).where(
                        ProducerFeedEntry.org_id == org_id,
                        or_(
                            and_(ProducerFeedEntry.producer_kind == APP, ProducerFeedEntry.producer_id == app_id),
                            and_(ProducerFeedEntry.producer_kind == SPACE, ProducerFeedEntry.owner_app_id == app_id),
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
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
            erase_at = None if held else now
            if erase_at != row.erase_at:
                row.erase_at = erase_at
                row.legal_hold = held
                moved.append(row)
        await self._renumber([*moved, *relisted])
        await self._session.flush()
        return [self._scheduled(row, 'held' if held else 'released') for row in moved]

    async def discard_person(
        self, org_id: str, user_id: str, incarnation: str | None, *, by_user_id: str
    ) -> list[ProducerFeedEntry]:
        """A removed member's incarnations (or the one named) erased now instead of after the retention.

        The caller has made sure no legal hold stands. One already due is
        left as it is; it is returned with the rest.
        """
        now = datetime.now(timezone.utc)
        found = [
            row
            for row in await self._person_rows(user_id, org_id)
            if row.removed and row.erasure_done_at is None and incarnation in (None, row.incarnation)
        ]
        moved: list[ProducerFeedEntry] = []
        for row in found:
            if row.erase_at is not None and row.erase_at <= now:
                continue
            row.erase_at = now
            row.discarded_by_user_id = by_user_id
            row.discarded_at = now
            moved.append(row)
        await self._renumber(moved)
        await self._session.flush()
        self.scheduled.extend(self._scheduled(row, 'discarded') for row in moved)
        return found

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


class ProducerFeedDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    async def members_in(self, org_id: str, user_ids: list[str] | None) -> dict[str, Member]:
        """Which of these people (every one, for None) are members of an existing org, and whether active.

        A member is active unless their membership or their account is
        deactivated. An invitation is no member.
        """
        query = (
            select(OrgMembership.user_id, OrgMembership.status, User.deactivated_at, OrgMembership.id)
            .join(Organization, Organization.id == OrgMembership.org_id)
            .join(User, User.user_id == OrgMembership.user_id)
            .where(OrgMembership.org_id == org_id, OrgMembership.status.in_(('active', 'deactivated')))
        )
        found: dict[str, Member] = {}
        async with self.session_scope() as session:
            parts: list[Sequence[str] | None] = [None] if user_ids is None else list(chunks(user_ids))
            for part in parts:
                rows = await session.execute(query if part is None else query.where(OrgMembership.user_id.in_(part)))
                for user_id, state, deactivated_at, membership_id in rows.all():
                    found[str(user_id)] = Member(
                        active=state == 'active' and deactivated_at is None, incarnation=str(membership_id)
                    )
        return found

    async def accounts_in(self, user_ids: list[str]) -> set[str]:
        """Which of these accounts still exist."""
        found: set[str] = set()
        async with self.session_scope() as session:
            for part in chunks(user_ids):
                rows = await session.execute(select(User.user_id).where(User.user_id.in_(part)))
                found.update(str(user_id) for user_id in rows.scalars().all())
        return found

    async def org_exists(self, org_id: str) -> bool:
        async with self.session_scope() as session:
            return await session.get(Organization, org_id) is not None

    async def apps_in(self, org_id: str, app_ids: list[str] | None) -> list[str]:
        """Which of these apps (every one, for None) are registered in the organisation."""
        query = select(RegisteredClient.client_id).where(
            RegisteredClient.kind == APP, RegisteredClient.state == APPROVED, RegisteredClient.org_id == org_id
        )
        async with self.session_scope() as session:
            if app_ids is None:
                return [str(found) for found in (await session.execute(query)).scalars().all()]
            out: list[str] = []
            for part in chunks(app_ids):
                rows = await session.execute(query.where(RegisteredClient.client_id.in_(part)))
                out.extend(str(found) for found in rows.scalars().all())
            return out

    async def orgs_with_producers(self) -> list[str]:
        """Every organisation with a member, an app, or a producer the feed has not reported removed."""
        query: CompoundSelect = union(
            select(OrgMembership.org_id).where(OrgMembership.status == 'active'),
            select(RegisteredClient.org_id).where(RegisteredClient.kind == APP),
            select(ProducerFeedEntry.org_id).where(ProducerFeedEntry.removed.is_(False)),
        )
        async with self.session_scope() as session:
            return sorted(str(org_id) for org_id in (await session.execute(query)).scalars().all() if org_id)

    async def removed_in(self, org_id: str) -> list[ProducerFeedEntry]:
        """Every removed incarnation of the organisation's producers the feed still keeps, newest first."""
        async with self.session_scope() as session:
            rows = await session.execute(
                select(ProducerFeedEntry)
                .where(ProducerFeedEntry.org_id == org_id, ProducerFeedEntry.removed.is_(True))
                .order_by(ProducerFeedEntry.removed_at.desc().nulls_last())
            )
            return list(rows.scalars().all())

    async def removed_incarnations(self, org_id: str, user_id: str) -> list[ProducerFeedEntry]:
        """A person's removed incarnations in the organisation not yet erased: due later, or held."""
        now = datetime.now(timezone.utc)
        async with self.session_scope() as session:
            rows = await session.execute(
                select(ProducerFeedEntry)
                .where(
                    ProducerFeedEntry.org_id == org_id,
                    ProducerFeedEntry.producer_kind == PERSON,
                    ProducerFeedEntry.producer_id == user_id,
                    ProducerFeedEntry.removed.is_(True),
                    or_(ProducerFeedEntry.erase_at.is_(None), ProducerFeedEntry.erase_at > now),
                )
                .order_by(ProducerFeedEntry.removed_at)
            )
            return list(rows.scalars().all())

    async def mark(self, org_id: str, producer_kind: str, producer_id: str) -> None:
        """Mark producers by hand, as the triggers do with a change."""
        async with self.session_scope() as session:
            await session.execute(
                text('SELECT producer_feed_mark(:o, :k, :p)'), {'o': org_id, 'k': producer_kind, 'p': producer_id}
            )

    @asynccontextmanager
    async def numbering(self) -> AsyncIterator[FeedNumbering]:
        """The feed's lock, waited for, held until this transaction commits."""
        async with self.session_scope() as session:
            await session.execute(text('SELECT pg_advisory_xact_lock(:k)'), {"k": FEED_LOCK})
            yield FeedNumbering(session)

    async def after(self, seq: int, *, limit: int) -> list[ProducerFeedEntry]:
        """Rows numbered after *seq*, in order."""
        async with self.session_scope() as session:
            rows = await session.execute(
                select(ProducerFeedEntry)
                .where(ProducerFeedEntry.seq > seq)
                .order_by(ProducerFeedEntry.seq)
                .limit(limit)
            )
            return list(rows.scalars().all())

    async def seen(self, seqs: list[int]) -> None:
        """These rows were handed to the gateway: a removed one's retention starts now."""
        if not seqs:
            return
        async with self.session_scope() as session:
            await session.execute(
                update(ProducerFeedEntry)
                .where(
                    ProducerFeedEntry.seq.in_(seqs),
                    ProducerFeedEntry.removed.is_(True),
                    ProducerFeedEntry.seen_removed_at.is_(None),
                )
                .values(seen_removed_at=datetime.now(timezone.utc))
            )

    async def purge(self, *, older_than_days: int) -> int:
        """Delete removed rows erased that long ago — and erased-on-request ones once erased — once handed over.

        Counted from the erasure (`erase_at`), not the removal. A row the
        gateway has not been handed since it last changed stays. An
        inactive row that was not removed stays: its VM still exists,
        suspended, and the row is how the gateway knows it.
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=older_than_days)
        async with self.session_scope() as session:
            result = await session.execute(
                delete(ProducerFeedEntry).where(
                    ProducerFeedEntry.removed.is_(True),
                    ProducerFeedEntry.seen_removed_at.is_not(None),
                    ProducerFeedEntry.erase_at.is_not(None),
                    or_(
                        ProducerFeedEntry.erase_at < cutoff,
                        and_(ProducerFeedEntry.erased.is_(True), ProducerFeedEntry.erase_at <= now),
                    ),
                )
            )
            return int(result.rowcount or 0)  # type: ignore[attr-defined]
