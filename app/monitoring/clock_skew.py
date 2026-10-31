"""How far this process's clock is from the database's.

B8. Nothing measured this, and three mechanisms depend on it agreeing —
see `CLOCK_SKEW` in `metrics.py` for the list. The one that made it worth
building is revocation: `users.tokens_valid_after` is stamped by
Postgres and compared against a token's `iat`, stamped by whichever API
replica minted the token. A replica whose clock is ahead by more than the
watermark's one-second margin mints tokens that survive a revocation, and
nothing anywhere would say so.

**Measured rather than compensated.** The obvious alternative is to widen
the margin to cover plausible skew, and it is worse than it looks: the
self-service route signs the caller out and sends them back to sign in, so
a five-second margin means a person who reauthenticates promptly — an
OAuth round trip is two or three seconds — is refused the token they just
obtained, on a page that will tell them nothing useful. A margin big
enough to hide skew is big enough to break the flow it protects. So the
margin stays at one second and the skew becomes visible instead.

Sampled on its own small loop rather than at scrape time, because
`render()` is synchronous and this needs a query. Once a minute is far
more often than clocks drift and far cheaper than the pool metrics it
sits beside: one `SELECT now()`.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import logging

from app.db.sql.clock import ClockDB
from app.monitoring.metrics import CLOCK_SKEW

logger = logging.getLogger(__name__)

#: How often to look. Clocks drift slowly; NTP steps them abruptly. A
#: minute catches a step within a minute and costs one query.
_INTERVAL_SECONDS = 60

#: Above this, say something. It is the revocation watermark's margin,
#: which makes it the point where skew stops being cosmetic and starts
#: being the difference between a withdrawn session and a live one.
WARN_SECONDS = 1.0


async def measure() -> float | None:
    """Seconds this process is ahead of the database, or None if unasked.

    Positive means the app is ahead — the direction that lets a token
    outlive its revocation. The app clock is read *after* the query
    returns rather than before it, so the round trip counts against the
    app rather than in its favour: an overstated skew is the safe error.
    """
    db_now = await ClockDB().now()
    if db_now is None:
        return None
    if db_now.tzinfo is None:
        db_now = db_now.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - db_now).total_seconds()


async def sample_once() -> float | None:
    """Measure, publish, and complain if it is large enough to matter."""
    skew = await measure()
    if skew is None:
        return None

    CLOCK_SKEW.set(skew)
    if abs(skew) > WARN_SECONDS:
        logger.warning(
            'Clock skew against the database is %.2fs (threshold %.1fs). '
            'Above this, a token minted by this process can outlive a session '
            'withdrawal, the audit trail can be mis-ordered, and retention '
            'windows are applied against the wrong day. Check NTP on this host.',
            skew,
            WARN_SECONDS,
        )
    return skew


async def _loop() -> None:
    while True:
        try:
            await sample_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never end the loop. A gauge that stopped updating looks
            # exactly like a clock that stopped drifting.
            logger.exception('Clock-skew sample failed; will try again')
        await asyncio.sleep(_INTERVAL_SECONDS)


def start(app_state: dict[str, asyncio.Task[None]]) -> None:
    """Start sampling. Unconditional, unlike retention's loop.

    Retention only runs when a window is configured; there is no
    equivalent switch here, because a deployment that has turned nothing
    on still has clocks and still revokes sessions.
    """
    app_state['clock_skew_task'] = asyncio.create_task(_loop())


async def stop(app_state: dict[str, asyncio.Task[None]]) -> None:
    """Cancel the loop and wait for it to unwind.

    The await matters for retention's reason: without it the event loop
    closes first and the shutdown log carries "Task was destroyed but it
    is pending" instead of nothing.
    """
    task = app_state.pop('clock_skew_task', None)
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        try:
            await task
        except Exception:
            # Shutdown continues either way, but silently is the wrong
            # way: a loop that died of something other than the cancel
            # we just sent is the only evidence anyone will ever get
            # that it stopped sampling before we asked it to.
            logger.exception('Clock-skew loop did not unwind cleanly')
