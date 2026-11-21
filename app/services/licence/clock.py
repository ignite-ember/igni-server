"""The time the licence is evaluated at, and why setting the clock back does not set the licence back.

The licence is offline, so the time is the customer's: a deployment that
winds its clock back past `expires` would otherwise be valid again. Each
component therefore keeps the latest time it has seen (`HighWaterMark`)
in its own database (`ClockStore`) and evaluates the licence at

    max(now, seen - CLOCK_TOLERANCE, the licence's own issued)

With a correct clock the mark is never ahead of it, and nothing changes.

The mark only moves on while this process's clock and the database's
agree within `CLOCK_AGREEMENT` (`LicenceClock`), so one clock set wrongly
into the future does not lapse a licence for good: correcting it puts
everything back. Setting both back together does not help either, because
the mark is already written down.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
import logging
import threading
import time
from typing import Protocol

logger = logging.getLogger(__name__)

#: How far the clock may fall behind the latest time seen before the licence stops following it back.
#: A day: wider than any correction NTP makes, far narrower than any lapse.
CLOCK_TOLERANCE = timedelta(hours=24)
#: How close this process's clock and its database's must be for a time to count as seen.
CLOCK_AGREEMENT = timedelta(hours=48)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class HighWaterMark:
    """The latest time this deployment has seen. Held in memory, so asking it costs nothing."""

    def __init__(self, seen: datetime | None = None) -> None:
        self._lock = threading.Lock()
        self._seen = seen

    @property
    def seen(self) -> datetime | None:
        return self._seen

    def raise_to(self, seen: datetime) -> None:
        """Move the mark on to `seen`. Never back."""
        with self._lock:
            if self._seen is None or seen > self._seen:
                self._seen = seen

    def evaluate_at(self, now: datetime) -> datetime:
        """`now`, unless it is further behind the mark than the tolerance allows."""
        seen = self._seen
        if seen is None:
            return now
        return max(now, seen - CLOCK_TOLERANCE)

    def behind(self, now: datetime) -> datetime | None:
        """The time already seen, when `now` is more than the tolerance behind it; otherwise None."""
        seen = self._seen
        if seen is None or now >= seen - CLOCK_TOLERANCE:
            return None
        return seen

    @staticmethod
    def agreed(now: datetime, database_now: datetime) -> datetime | None:
        """The time both clocks vouch for (the earlier), or None when they are further apart than `CLOCK_AGREEMENT`."""
        if abs(now - database_now) > CLOCK_AGREEMENT:
            return None
        return min(now, database_now)

    @staticmethod
    def believable(seen: datetime, *, database_now: datetime) -> bool:
        """Whether a time an earlier release wrote down is one the database's clock could have reached."""
        return seen <= database_now + CLOCK_AGREEMENT

    @staticmethod
    def warning(seen: datetime) -> str:
        """What the admin is told while the clock is behind a time already seen."""
        at = seen.astimezone(timezone.utc)
        return (
            f'The system clock is earlier than a time this deployment has already seen ({at:%Y-%m-%d %H:%M} UTC). '
            'The licence is evaluated at that time; fix the clock.'
        )


class ClockStore(Protocol):
    """Where a component writes its mark down: its own database."""

    async def database_now(self) -> datetime:
        """The database's own clock."""
        ...

    async def stored(self) -> datetime | None:
        """The mark written down, or None before the first one."""
        ...

    async def seed(self) -> datetime | None:
        """The newest time the component recorded before it kept a mark (its audit trail), for the first start."""
        ...

    async def advance(self, seen: datetime) -> datetime:
        """Raise the mark written down to `seen` (never lower it) and return what is written down now."""
        ...


class LicenceClock:
    """Keeps a component's `HighWaterMark` in step with its database: loaded at start, moved on as time passes."""

    #: How often the time is written down. A rollback inside it loses at most this much, far within the tolerance.
    INTERVAL = timedelta(minutes=10)
    #: How often a clock that is behind, or that disagrees with the database, is logged again.
    WARN_EVERY = timedelta(hours=1)

    def __init__(self, mark: HighWaterMark, store: ClockStore, *, clock: Clock | None = None) -> None:
        self._mark = mark
        self._store = store
        self._clock = clock or SystemClock()
        self._synced_at: float | None = None
        self._warned_at: dict[str, float] = {}
        self._task: asyncio.Task[None] | None = None

    @property
    def mark(self) -> HighWaterMark:
        return self._mark

    async def sync(self) -> datetime | None:
        """Raise the mark from the database, and the database's from this clock while the two agree."""
        database_now = await self._store.database_now()
        now = self._clock.now()
        stored = await self._store.stored()
        if stored is None:
            seed = await self._store.seed()
            if seed is not None and HighWaterMark.believable(seed, database_now=database_now):
                stored = await self._store.advance(seed)
        agreed = HighWaterMark.agreed(now, database_now)
        if agreed is None:
            self._warn(
                'disagree',
                'licence: this process\'s clock (%s) and the database\'s (%s) are more than %s apart, so the time '
                'this deployment has seen is not moved on until they agree',
                now.isoformat(),
                database_now.isoformat(),
                CLOCK_AGREEMENT,
            )
        else:
            stored = await self._store.advance(agreed)
        if stored is not None:
            self._mark.raise_to(stored)
        seen = self._mark.behind(now)
        if seen is not None:
            self._warn('behind', 'licence: %s', HighWaterMark.warning(seen))
        self._synced_at = time.monotonic()
        return self._mark.seen

    async def sync_if_due(self) -> None:
        """`sync`, unless this process did within `INTERVAL`. For a worker about to ask the licence. Never raises."""
        if self._synced_at is not None and time.monotonic() - self._synced_at < self.INTERVAL.total_seconds():
            return
        await self._sync_quietly()

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name='licence-clock')

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.INTERVAL.total_seconds())
            await self._sync_quietly()

    async def _sync_quietly(self) -> None:
        try:
            await self.sync()
        except Exception:  # noqa: BLE001 — the licence is still evaluated on the mark already held
            logger.warning('licence: the time this deployment has seen could not be read or written', exc_info=True)

    def _warn(self, key: str, message: str, *args: object) -> None:
        last = self._warned_at.get(key)
        if last is not None and time.monotonic() - last < self.WARN_EVERY.total_seconds():
            return
        self._warned_at[key] = time.monotonic()
        logger.warning(message, *args)
