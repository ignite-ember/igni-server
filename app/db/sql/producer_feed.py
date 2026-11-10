"""The gateway's feed of producers, on the shared plane: who exists, paging, retention, and the lock.

What is numbered under the lock is `FeedNumbering` (the drain) and
`FeedErasures` (erasures moved); see `app.db.sql.models.producer_feed`
for why `seq` is safe to page by.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, CompoundSelect, delete, or_, select, text, union, update

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.bind_chunks import chunks
from app.db.sql.feed_erasures import FeedErasures
from app.db.sql.feed_lock import FeedLock
from app.db.sql.feed_numbering import FeedNumbering
from app.db.sql.feed_transaction import FeedTransaction
from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.models.organization import Organization
from app.db.sql.models.producer_feed import PERSON, ProducerFeedEntry
from app.db.sql.models.registered_client import APP, APPROVED, RegisteredClient
from app.db.sql.models.user import User
from app.db.sql.schemas.producer_feed import Member
from app.utils.singleton import SingletonMeta


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
        """The drain's transaction: the feed's lock, waited for, held until it commits."""
        async with self._locked(FeedNumbering) as numbering:
            yield numbering

    @asynccontextmanager
    async def erasures(self) -> AsyncIterator[FeedErasures]:
        """A transaction moving erasures: the feed's lock, waited for, held until it commits."""
        async with self._locked(FeedErasures) as erasures:
            yield erasures

    @asynccontextmanager
    async def _locked[T: FeedTransaction](self, kind: type[T]) -> AsyncIterator[T]:
        async with self.session_scope() as session:
            await FeedLock.take(session)
            transaction = kind(session)
            yield transaction
            await transaction.close()

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
