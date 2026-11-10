"""What every transaction holding the feed's lock does: draw numbers, revoke, and schedule erasures."""

from __future__ import annotations

from datetime import datetime, timezone
import math

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql.models.producer_feed import ProducerFeedEntry
from app.db.sql.revocations import RevocationsDB
from app.db.sql.schemas.producer_feed import RevocationEntry, Scheduled


class FeedTransaction:
    """A transaction holding the feed's lock: the only place feed numbers are drawn.

    Producers it stops are revoked in the same transaction, by `close`,
    so a revocation is numbered under the lock too (§21).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._revoked: list[RevocationEntry] = []

    async def close(self) -> None:
        """Store what was revoked; called once, before the transaction commits."""
        await RevocationsDB.add_in(self._session, self._revoked)

    def _revoke(self, row: ProducerFeedEntry, reason: str) -> None:
        """This producer made inactive, removed or erased: its tokens stop."""
        self._revoked.append(
            RevocationEntry(
                kind=row.producer_kind,
                org_id=row.org_id,
                subject_id=row.producer_id,
                incarnation=row.incarnation,
                not_before_ms=math.ceil(datetime.now(timezone.utc).timestamp() * 1000),
                reason=reason,
            )
        )

    async def _numbers(self, count: int) -> list[int]:
        rows = await self._session.execute(
            select(func.nextval('producer_feed_seq')).select_from(func.generate_series(1, count))
        )
        return sorted(int(n) for n in rows.scalars().all())

    async def _renumber(self, rows: list[ProducerFeedEntry]) -> None:
        """List these rows again: each gets the next number."""
        numbers = iter(await self._numbers(len(rows))) if rows else iter([])
        now = datetime.now(timezone.utc)
        for row in rows:
            row.changed_at = now
            row.seen_removed_at = None
            row.seq = next(numbers)

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
