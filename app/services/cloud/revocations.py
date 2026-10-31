"""Revocations for parties that check tokens by their signature (`docs/service-identity.md` §21).

igni server refuses a revoked token on its own next request; the gateway
and services, which check a token's signature and do not ask igni server,
learn of revocations here: a producer made inactive or removed (numbered
by the feed as it happens) and a person's sessions withdrawn. Each row
revokes every token for its subject issued at or before `not_before_ms`.
Long-poll capable, like the feed.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.api.base_v1.schema.producer_feed import RevocationItem, RevocationPage
from app.db.sql.models.revocation import Revocation
from app.db.sql.revocations import RevocationsDB
from app.db.sql.schemas.producer_feed import RevocationEntry
from app.services.auth.jwt_service import PERSON, SESSION_LIFETIME
from app.services.cloud.long_poll import LongPoll
from app.services.cloud.producer_feed import FeedCursor, ProducerFeed

#: Kept past the longest a token they could stop lives.
KEEP = SESSION_LIFETIME + timedelta(days=1)


class Revocations:
    def __init__(self) -> None:
        self._db = RevocationsDB()

    @staticmethod
    def item(row: Revocation) -> RevocationItem:
        return RevocationItem(
            kind=row.kind,
            org_id=row.org_id,
            subject_id=row.subject_id,
            incarnation=row.incarnation,
            not_before_ms=row.not_before_ms,
            reason=row.reason,
        )

    async def page(self, since: str | None, *, org_ids: list[str] | None, limit: int, wait: float) -> RevocationPage:
        """After *since*: every revocation (the gateway's), or some organisations' (a service's)."""
        after = FeedCursor.parse(since)

        async def fetch() -> list[Revocation]:
            await ProducerFeed().drain()
            return await self._db.after(after, org_ids=org_ids, limit=limit)

        rows = await LongPoll.run(fetch, wait=wait)
        return RevocationPage(revocations=[self.item(row) for row in rows], cursor=str(rows[-1].seq if rows else after))

    async def sessions_withdrawn(self, user_id: str, watermark: datetime) -> None:
        """A person's every session ended — in every organisation."""
        await self._db.add(
            [
                RevocationEntry(
                    kind=PERSON,
                    org_id=None,
                    subject_id=user_id,
                    not_before_ms=int(watermark.timestamp() * 1000),
                    reason='sessions_withdrawn',
                )
            ]
        )

    async def close_out(self) -> int:
        return await self._db.purge(older_than=KEEP)
