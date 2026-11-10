"""Events per service per minute, counted in Redis (`SERVICE_EVENTS_PER_MINUTE`)."""

from __future__ import annotations

from datetime import datetime, timezone

import redis.asyncio as aioredis

from app.config import settings
from app.db.redis.keys import key
from app.services.cloud.producer_event_errors import TooManyEvents

#: A minute's counter outlives its minute by one more, then Redis drops it.
BUCKET_TTL_SECONDS = 120


class EventRateLimit:
    """One request's worth of a service's minute, items and attachment uploads alike.

    Given a Redis client, it uses that one and leaves it open; without, it
    opens one for the count and closes it again.
    """

    def __init__(self, *, redis: aioredis.Redis | None = None, per_minute: int | None = None) -> None:
        self._redis = redis
        self._per_minute = per_minute

    @property
    def limit(self) -> int:
        return settings.service_events_per_minute if self._per_minute is None else self._per_minute

    async def spend(self, client_id: str) -> None:
        """Count one; `TooManyEvents` once past the limit, with the seconds until the next minute."""
        limit = self.limit
        if limit <= 0:
            return
        now = datetime.now(timezone.utc)
        count = await self._count(key('service_events', client_id, now.strftime('%Y%m%d%H%M')))
        if count > limit:
            raise TooManyEvents(retry_after=60 - now.second)

    async def _count(self, bucket: str) -> int:
        if self._redis is not None:
            return await self._incr(self._redis, bucket)
        client = aioredis.Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            return await self._incr(client, bucket)
        finally:
            await client.aclose()

    @staticmethod
    async def _incr(client: aioredis.Redis, bucket: str) -> int:
        count = int(await client.incr(bucket))
        if count == 1:
            await client.expire(bucket, BUCKET_TTL_SECONDS)
        return count
