"""Applying the retention window, on a schedule.

DP-6 in `COMPLIANCE_TRACKER.md` — GDPR Article 5(1)(e). A retention
*setting* is not a retention *limit*: SOC 2 asks whether a control
operates, not whether it could. `AUDIT_RETENTION_DAYS` needed something
to apply it.

## Why here and not a scheduler process

`celery beat` was the conventional answer and it needed another process
in the deployment. F55 had already found the Celery worker serving a task
nothing dispatched, and adding a third container to that argument was the
wrong move; DEC-13 then removed the worker outright, so a scheduler would
now mean reintroducing the whole queue for one daily `DELETE`.

Which is what this module is the evidence for: the one piece of periodic
work the product actually has runs on an asyncio task inside the API and
needs nothing else.

## Only one instance does the work

The loop runs in every API replica, so it takes a Postgres advisory lock
before pruning and skips the round if another instance holds it. Advisory
rather than a row: it releases on its own if a process dies, so there is
no stale-lock state to clean up. The same choice, for the same reason, as
F86's guard on the test database.

## It does nothing unless configured

The audit window defaults to 0, meaning keep everything — a deployment
that has not made a retention decision gets no surprise deletions of its
audit trail; Article 5(1)(e) asks for a limit, not for deletion by
default. Events for producers are different: they are deliveries, not
records, and their service reads what became of each for
`SERVICE_EVENT_RETENTION_DAYS` (30) after it was delivered, expired or
dropped, after which the same round deletes it — and marks as expired
whatever has passed its `expires_at`. With both at 0 the loop is not
started at all.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging

from pydantic import BaseModel

from app.config import settings
from app.db.sql.advisory_locks import AdvisoryLockDB
from app.db.sql.audit_events import AuditEventDB
from app.monitoring import note_retention_success, RETENTION_FAILURES
from app.services.cloud.producer_events import ProducerEvents
from app.services.cloud.producer_exports import ProducerExports
from app.services.cloud.producer_feed import ProducerFeed
from app.services.cloud.revocations import Revocations

logger = logging.getLogger(__name__)


class RetentionRun(BaseModel):
    """What one round removed, per table.

    The count is zero when another replica held the lock, which is
    indistinguishable here from a round that found nothing to delete —
    deliberately, because to every caller the two mean the same thing:
    this instance removed nothing and does not need to say why.
    """

    audit_events: int = 0
    #: Events for producers that were delivered, expired or dropped more
    #: than `SERVICE_EVENT_RETENTION_DAYS` ago (`docs/service-identity.md` §15).
    producer_events: int = 0
    #: Rows of the gateway's feed for producers removed by their owner,
    #: handed to the gateway more than `PRODUCER_FEED_RETENTION_DAYS` ago, or erased
    #: (`docs/service-identity.md` §11.3).
    producer_feed: int = 0
    #: Exports of removed members' data past `PRODUCER_EXPORT_RETENTION_DAYS`.
    producer_exports: int = 0
    #: Revocations older than any token they could stop.
    revocations: int = 0

    def removed_anything(self) -> bool:
        return bool(
            self.audit_events or self.producer_events or self.producer_feed or self.producer_exports or self.revocations
        )


# Arbitrary, stable, and distinct from any other advisory lock this
# project takes.
_RETENTION_LOCK_KEY = 0x1911_2_5445

_INTERVAL_SECONDS = 24 * 60 * 60

# A short delay before the first run so startup is not competing with
# migrations or the first requests.
_FIRST_RUN_DELAY_SECONDS = 60


async def _claim() -> bool:
    """True when this instance may prune this round."""
    return await AdvisoryLockDB().try_acquire(_RETENTION_LOCK_KEY)


async def _release() -> None:
    await AdvisoryLockDB().release(_RETENTION_LOCK_KEY)


async def run_once() -> RetentionRun:
    """Apply the window once. Returns what was removed.

    Separated from the loop so it is callable directly — by a test, or by
    an operator who would rather drive this from cron than trust a
    background task.
    """
    removed = RetentionRun()

    if not await _claim():
        logger.debug('Retention: another instance holds the lock, skipping this round')
        return removed

    try:
        if settings.audit_retention_days > 0:
            removed.audit_events = await AuditEventDB().prune(older_than_days=settings.audit_retention_days)
        if settings.service_event_retention_days > 0:
            removed.producer_events = await ProducerEvents().close_out()
        if settings.producer_feed_retention_days > 0:
            removed.producer_feed = await ProducerFeed().close_out()
        removed.producer_exports = await ProducerExports().close_out()
        removed.revocations = await Revocations().close_out()
    finally:
        # Released even on failure: holding it would silently stop every
        # other instance from ever pruning, which is the failure mode
        # hardest to notice.
        #
        # Which is also why this logs. Suppressing the release failure
        # without a word is the exact outcome the paragraph above is
        # afraid of, and it used to do that.
        try:
            await _release()
        except Exception:
            logger.exception(
                'Retention could not release the advisory lock. Until the holding session ends, no replica will prune.'
            )

    return removed


def is_configured() -> bool:
    """Whether any window is set. The loop is not started otherwise."""
    return (
        settings.audit_retention_days > 0
        or settings.service_event_retention_days > 0
        or settings.producer_feed_retention_days > 0
    )


async def _loop() -> None:
    await asyncio.sleep(_FIRST_RUN_DELAY_SECONDS)
    while True:
        try:
            removed = await run_once()
            # A timestamp rather than a counter, and set here rather than
            # inside `run_once`: what an alert needs to know is "when did
            # this last finish", so that a runner which stopped a week
            # ago is visible without anybody having recorded a rate.
            note_retention_success()
            if removed.removed_anything():
                logger.info('Retention removed %s', removed.model_dump())
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let one bad round end the loop: the next one is a day
            # away and a deployment that stopped pruning silently is
            # exactly the gap this exists to close.
            RETENTION_FAILURES.inc()
            logger.exception('Retention round failed; will try again')
        await asyncio.sleep(_INTERVAL_SECONDS)


def start(app_state: dict[str, asyncio.Task[None]]) -> None:
    """Start the loop if any window is configured."""
    if not is_configured():
        logger.info(
            'Retention: no window configured (AUDIT_RETENTION_DAYS and SERVICE_EVENT_RETENTION_DAYS are 0) '
            '— nothing will be deleted'
        )
        return

    app_state['retention_task'] = asyncio.create_task(_loop())
    logger.info(
        'Retention active: audit=%sd producer events=%sd',
        settings.audit_retention_days,
        settings.service_event_retention_days,
    )


async def stop(app_state: dict[str, asyncio.Task[None]]) -> None:
    """Cancel the loop on shutdown, and wait for it to notice.

    Without the await, the task is cancelled and the event loop closes
    before it unwinds, which surfaces as "Task was destroyed but it is
    pending" and an unreleased advisory lock.
    """
    task = app_state.pop('retention_task', None)
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        try:
            await task
        except Exception:
            # Shutdown proceeds regardless, but a loop that ended of
            # something other than our cancel took the advisory lock's
            # session with it, and this is the only place that would
            # ever say so.
            logger.exception('Retention loop did not unwind cleanly')
