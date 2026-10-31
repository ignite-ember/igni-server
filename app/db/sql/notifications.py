"""Writing and reading what is waiting on somebody.

Queries, and the one rule the database is the only thing that can
enforce: **never twice for one event.** An ``event_key`` names the thing
that happened; a retry, or two paths that both decide you should hear
about it, write one row. Settled by ``INSERT … ON CONFLICT DO NOTHING``
against the partial unique index rather than a read-then-write, which
two workers would both win.

*Who* is told is settled before anything reaches here — see
:class:`~app.services.notifications.delivery.NotificationDelivery`.

Rows live in the recipient's organisation's own database. Reading starts
from the person instead, so it reads every organisation they are an
active member of and merges: a person in two organisations has one list,
one count and one "mark all read". An organisation they have left is
not read at all, and a person in none has nothing waiting.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
from typing import Any, cast
import uuid

from sqlalchemy import func, select, Update, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult

from app.db.sql.models.notification import Notification
from app.db.sql.schemas.notification import NotificationCounts, NotificationDraft, NotificationRef
from app.db.sql.tenancy import TenantData

__all__ = ['MAX_NOTIFICATIONS', 'NotificationsDB']

#: How many a machine may ask for at once.
MAX_NOTIFICATIONS = 100

#: Where a row with no time sorts: last.
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


class NotificationsDB(TenantData):
    """What is waiting on each account."""

    async def tell(self, *, org_id: str, user_ids: list[str], draft: NotificationDraft) -> list[NotificationRef]:
        """Write ``draft`` once for each of these people, in ``org_id``.

        Answers the rows actually written: somebody already told about
        this ``event_key`` by this source gets nothing new.
        """
        if not user_ids:
            return []
        now = datetime.now(timezone.utc)
        rows = [draft.row_for(user_id, notification_id=str(uuid.uuid4()), created_at=now) for user_id in user_ids]
        statement = (
            insert(Notification)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=['user_id', 'source', 'event_key'],
                index_where=Notification.event_key.isnot(None),
            )
            .returning(Notification)
        )
        async with self.session_scope(org_id) as session:
            written = (await session.execute(statement)).scalars().all()
            return [NotificationRef.from_row(row, org_id=org_id) for row in written]

    async def deliver(
        self, *, user_id: str, unread_only: bool = False, source: str = '', limit: int = MAX_NOTIFICATIONS
    ) -> list[NotificationRef]:
        """Somebody's notifications from every organisation they are in, newest first — and now delivered.

        Each organisation's database answers its newest ``limit``, the
        lists are merged, and the newest ``limit`` of all of them are
        answered. Listing is delivery: only those, being on the person's
        machine now, are marked delivered — what was read but cut by the
        merge is not.
        """
        orgs = await self.provisioned_orgs_of(user_id)
        found = await asyncio.gather(
            *(self._newest(org_id, user_id, unread_only=unread_only, source=source, limit=limit) for org_id in orgs)
        )
        # Newest first, ties by id: the order each database answers in.
        merged = sorted((ref for refs in found for ref in refs), key=lambda ref: ref.id)
        merged.sort(key=lambda ref: ref.created_at or _EPOCH, reverse=True)
        answered = merged[:limit]
        now = datetime.now(timezone.utc)
        undelivered: dict[str, list[str]] = {}
        for ref in answered:
            if ref.delivered_at is None:
                undelivered.setdefault(ref.org_id, []).append(ref.id)
        await asyncio.gather(*(self._mark_delivered(org_id, ids, now) for org_id, ids in undelivered.items()))
        return [ref if ref.delivered_at else ref.model_copy(update={'delivered_at': now}) for ref in answered]

    async def _newest(
        self, org_id: str, user_id: str, *, unread_only: bool, source: str, limit: int
    ) -> list[NotificationRef]:
        statement = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            statement = statement.where(Notification.read_at.is_(None))
        if source:
            statement = statement.where(Notification.source == source)
        statement = statement.order_by(Notification.created_at.desc(), Notification.id).limit(limit)
        async with self.session_scope(org_id) as session:
            rows = (await session.execute(statement)).scalars().all()
            return [NotificationRef.from_row(row, org_id=org_id) for row in rows]

    async def _mark_delivered(self, org_id: str, ids: list[str], now: datetime) -> None:
        async with self.session_scope(org_id) as session:
            await session.execute(
                update(Notification)
                .where(Notification.id.in_(ids), Notification.delivered_at.is_(None))
                .values(delivered_at=now)
                .execution_options(synchronize_session=False)
            )

    async def counts(self, *, user_id: str) -> NotificationCounts:
        """Unread, in all and per source, summed over every organisation the person is in.

        Counted, not measured off a capped list.
        """
        orgs = await self.provisioned_orgs_of(user_id)
        by_source: Counter[str] = Counter()
        for counted in await asyncio.gather(*(self._unread_by_source(org_id, user_id) for org_id in orgs)):
            by_source.update(counted)
        return NotificationCounts(unread=sum(by_source.values()), by_source=dict(by_source))

    async def _unread_by_source(self, org_id: str, user_id: str) -> dict[str, int]:
        async with self.session_scope(org_id) as session:
            result = await session.execute(
                select(Notification.source, func.count())
                .where(Notification.user_id == user_id, Notification.read_at.is_(None))
                .group_by(Notification.source)
            )
            return {str(source): int(count) for source, count in result.all()}

    async def mark_read(self, *, user_id: str, ids: list[str] | None = None, source: str = '') -> int:
        """Mark these, this source's, or everything read, in every organisation
        the person is in. Answers how many changed.

        Scoped to the account on the way in: an id is guessable and a
        notification is somebody else's business. Ids are not grouped by
        organisation first — each database marks the ones it holds.
        """
        if ids is not None and not ids:
            return 0
        statement = (
            update(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
            .values(read_at=datetime.now(timezone.utc))
        )
        if ids is not None:
            statement = statement.where(Notification.id.in_(ids))
        if source:
            statement = statement.where(Notification.source == source)
        orgs = await self.provisioned_orgs_of(user_id)
        return sum(await asyncio.gather(*(self._apply(org_id, statement) for org_id in orgs)))

    async def _apply(self, org_id: str, statement: Update) -> int:
        async with self.session_scope(org_id) as session:
            result = await session.execute(statement)
            # `execute` is annotated `Result`; an UPDATE returns a
            # `CursorResult`, which is where `rowcount` lives.
            return int(cast('CursorResult[Any]', result).rowcount or 0)
