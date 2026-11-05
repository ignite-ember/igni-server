"""The gateway's feed of new and changed producers (`docs/service-identity.md` §11.3).

The gateway provisions each producer's VM ahead of its first request,
and again when its profile changes, so it hears of every producer that
appears, goes, or gets another profile.

**Changes are marked as they happen.** Every table that decides what a
producer is — memberships, accounts, apps and their registrations,
groups and their members, profiles, the caps, VM credentials,
organisations — has a trigger that marks the producers a change touches,
in the transaction that made it. No code path can forget to: the
database does it.

**The feed numbers the marks.** When it is read, one transaction at a
time (an advisory lock held until it commits) reads the marks, works out
the marked producers' state now, and numbers each that differs from what
the feed last reported. Only that transaction draws numbers, so none is
committed after a larger one, and the gateway, paging by number, misses
nothing. The cost of a read is the number of changes, not of producers.

**A membership ending is numbered as it ends** (:meth:`ProducerFeed.removal`).
A mark names a producer; it does not keep the incarnation it was, and a
removed membership's row is gone — so a person who joined and was
removed between two reads would have no incarnation for the feed to
remove, no erasure, and nothing to export. Removing somebody catches the
feed up first, so their incarnation is listed, and numbers the removal
after, so it is removed, its erasure scheduled and the gateway told then.

**Reconciling** marks everybody and numbers what differs; the retention
round does it daily, and a difference it finds is a change some path
failed to mark — the tests hold that there is none.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging

from fastapi import HTTPException, status

from app.api.base_v1.schema.effective_vm_profile import EffectiveVmProfile
from app.api.base_v1.schema.producer_feed import ProducerFeedItem, ProducerFeedPage
from app.config import settings
from app.db.sql.models.app_space import SPACE
from app.db.sql.models.producer_feed import ProducerFeedEntry
from app.db.sql.models.registered_client import APP
from app.db.sql.producer_feed import FeedNumbering, PERSON, ProducerFeedDB
from app.db.sql.producer_feed_tenant import ProducerFeedTenantDB
from app.db.sql.revocations import FeedSignals, RevocationsDB
from app.db.sql.schemas.producer_feed import FeedMark
from app.db.sql.tenancy import TenantData
from app.security.producer import ProducerRef
from app.services.cloud.erasure_audit import ErasureAudit
from app.services.cloud.long_poll import LongPoll
from app.services.cloud.producer_states import ProducerStates
from app.services.cloud.vm_profiles import VmCaps

logger = logging.getLogger(__name__)

#: Every kind of producer, in the order a drain numbers them.
KINDS = (PERSON, APP, SPACE)

#: A mark naming every space of one app: `space` / `app:<app_id>`.
APP_SPACES = 'app:'

EVERY = '*'


class FeedCursor:
    """The opaque cursor: the last number seen."""

    @staticmethod
    def parse(since: str | None) -> int:
        if not since:
            return 0
        if not since.isdigit():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='since is not a cursor this feed gave')
        return int(since)


class Targets:
    """What a batch of marks names, per organisation: some producers, or every one of a kind."""

    def __init__(self) -> None:
        self.ids: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        self.every: dict[str, set[str]] = defaultdict(set)
        #: Apps every space of which is named.
        self.apps: dict[str, set[str]] = defaultdict(set)
        self.platform = False

    def add(self, mark: FeedMark) -> None:
        if mark.org_id == EVERY:
            self.platform = True
            return
        if mark.producer_kind == EVERY:
            self.every[mark.org_id].update(KINDS)
        elif mark.producer_id == EVERY:
            self.every[mark.org_id].add(mark.producer_kind)
        elif mark.producer_kind == SPACE and mark.producer_id.startswith(APP_SPACES):
            self.apps[mark.org_id].add(mark.producer_id.removeprefix(APP_SPACES))
        else:
            self.ids[mark.org_id][mark.producer_kind].add(mark.producer_id)

    def everybody_in(self, org_id: str) -> None:
        self.every[org_id].update(KINDS)

    def orgs(self) -> list[str]:
        return sorted(set(self.ids) | set(self.every) | set(self.apps))


class ProducerFeed:
    def __init__(self) -> None:
        self._db = ProducerFeedDB()
        self._tenant = ProducerFeedTenantDB()

    async def drain(self) -> int:
        """Number the producers the marks name whose state changed. Returns how many."""
        tenant_marks: dict[str, list[FeedMark]] = {}
        async with self._db.numbering() as numbering:
            marks = await numbering.marks()
            for org_id in await TenantData().each_tenant():
                found = await self._tenant.marks(org_id)
                if found:
                    tenant_marks[org_id] = found
            targets = Targets()
            for mark in [*marks, *(m for found in tenant_marks.values() for m in found)]:
                targets.add(mark)
            if targets.platform:
                for org_id in await self._db.orgs_with_producers():
                    targets.everybody_in(org_id)
            changed = await self._number(numbering, targets)
            await numbering.forget(marks)
            await RevocationsDB.add_in(numbering.session, numbering.revoked)
        for org_id, found in tenant_marks.items():
            await self._tenant.forget(org_id, found)
        await ErasureAudit().record(numbering.scheduled)
        if changed:
            await FeedSignals().notify()
        return changed

    @asynccontextmanager
    async def removal(self) -> AsyncIterator[None]:
        """Around deleting memberships: the incarnations they end are listed before and removed after."""
        await self.drain()
        yield
        await self.drain()

    async def _number(self, numbering: FeedNumbering, targets: Targets) -> int:
        caps = await VmCaps().get()
        changed = 0
        for org_id in targets.orgs():
            states = ProducerStates(org_id, caps)
            for kind in KINDS:
                ids = set(targets.ids[org_id][kind]) if org_id in targets.ids else set()
                if kind in targets.every.get(org_id, set()):
                    ids.update(await states.current(kind))
                    ids.update(await numbering.live_ids(org_id, kind))
                if kind == SPACE:
                    for app_id in targets.apps.get(org_id, set()):
                        ids.update(await states.spaces_of(app_id))
                if ids:
                    listed = sorted(ids)
                    changed += await numbering.record(org_id, kind, listed, await states.of(kind, listed))
        return changed

    async def started(self) -> int:
        """At start: a redeploy may have changed the deployment's caps (`settings.cloud_vm`).

        They are in force until a super admin sets caps, and no trigger
        sees a setting change, so the feed works every producer out afresh
        then. Caps a super admin set are a row, which marks on change.
        """
        if not await VmCaps().from_deployment():
            return 0
        return await self.reconcile()

    async def started_in_background(self) -> None:
        """`started`, for the lifespan: a failure is logged, never raised."""
        try:
            changed = await self.started()
        except Exception:  # noqa: BLE001 — the daily reconcile catches up
            logger.exception('Could not reconcile the producers feed with the deployment caps at start')
            return
        if changed:
            logger.info('The deployment caps changed %d producers; the feed lists them again', changed)

    async def reconcile(self) -> int:
        """Work out every producer afresh and number what differs: what no mark should have missed."""
        await self._db.mark(EVERY, EVERY, EVERY)
        return await self.drain()

    @staticmethod
    def item(row: ProducerFeedEntry) -> ProducerFeedItem:
        return ProducerFeedItem(
            org_id=row.org_id,
            producer=ProducerRef(kind=row.producer_kind, id=row.producer_id),  # type: ignore[arg-type]
            incarnation=row.incarnation,
            active=row.active,
            removed=row.removed,
            profile_version=row.profile_version,
            packages_version=row.packages_version,
            profile=(
                None if row.removed or row.vm_profile is None else EffectiveVmProfile.model_validate(row.vm_profile)
            ),
            volume_gib=row.volume_gib,
            erase_at=row.erase_at,
            owner=ProducerRef(kind=APP, id=row.owner_app_id) if row.owner_app_id else None,
            legal_hold=row.legal_hold,
            changed_at=row.changed_at,
        )

    async def page(self, since: str | None, *, limit: int, wait: float = 0) -> ProducerFeedPage:
        """What changed after *since*, oldest change first; a producer may appear again.

        With *wait*, a long poll: answered as soon as there is a change.
        """
        after = FeedCursor.parse(since)

        async def fetch() -> list[ProducerFeedEntry]:
            await self.drain()
            return await self._db.after(after, limit=limit)

        rows = await LongPoll.run(fetch, wait=wait)
        await self._db.seen([row.seq for row in rows if row.removed])
        return ProducerFeedPage(producers=[self.item(row) for row in rows], cursor=str(rows[-1].seq if rows else after))

    async def close_out(self) -> int:
        """The daily round: reconcile, then delete what the gateway saw go long enough ago."""
        await self.reconcile()
        return await self._db.purge(older_than_days=settings.producer_feed_retention_days)

    async def erase_person(self, user_id: str) -> None:
        """A person erased on request: every incarnation removed and erased now, overriding retention."""
        await self.drain()
        async with self._db.numbering() as numbering:
            scheduled = await numbering.erase_person(user_id)
            await RevocationsDB.add_in(numbering.session, numbering.revoked)
        await ErasureAudit().record(scheduled)
        await FeedSignals().notify()

    async def reschedule_app(self, org_id: str, app_id: str, *, held: bool) -> None:
        """A legal hold placed on an app or released: the app's rows and its spaces'."""
        await self.drain()
        async with self._db.numbering() as numbering:
            scheduled = await numbering.reschedule_app(org_id, app_id, held=held)
        await ErasureAudit().record(scheduled)
        await FeedSignals().notify()

    async def discard_person(self, org_id: str, user_id: str, incarnation: str | None, *, by_user_id: str) -> None:
        """A removed member's VM erased now: its `erase_at` is moved to now, and the gateway hears at once."""
        async with self._db.numbering() as numbering:
            await numbering.discard_person(org_id, user_id, incarnation, by_user_id=by_user_id)
        await ErasureAudit().record(numbering.scheduled)
        await FeedSignals().notify()

    async def discarded_app(self, org_id: str, app_id: str, *, name: str, by_user_id: str) -> None:
        """An app just removed by its discard: listed removed at once, keeping its name for the portal."""
        await self.drain()
        async with self._db.numbering() as numbering:
            await numbering.label_discarded(org_id, APP, app_id, label=name, by_user_id=by_user_id)
        await FeedSignals().notify()

    async def erasure_done(
        self, org_id: str, kind: str, producer_id: str, incarnation: str
    ) -> tuple[ProducerFeedEntry | None, bool]:
        """The gateway erased a removed incarnation's VM. Whether this report is the first."""
        async with self._db.numbering() as numbering:
            before = await numbering.erasure_state(org_id, kind, producer_id, incarnation)
            row = await numbering.erasure_done(org_id, kind, producer_id, incarnation)
        return row, row is not None and before is None

    async def reschedule(self, org_id: str, user_id: str, *, held: bool) -> None:
        """A legal hold placed or released on a person in one organisation."""
        await self.drain()
        async with self._db.numbering() as numbering:
            scheduled = await numbering.reschedule(org_id, user_id, held=held)
        await ErasureAudit().record(scheduled)
        # The person's live row carries the hold too (§12.3): the gateway hears of it now.
        await FeedSignals().notify()
