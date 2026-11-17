"""The retention loop: a bad round neither ends it nor goes unnoticed.

`run_once` is tested against the database in `test_retention_and_erasure`.
This is the loop around it — what a deployment relies on to keep pruning
for months with nobody watching: a round that raises is counted and the
next one still runs, a round that finishes is timestamped for the alert
that notices a runner which stopped, and shutdown waits for the loop.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.config import settings
from app.monitoring.metrics import RETENTION_FAILURES, RETENTION_LAST_SUCCESS
from app.services import retention
from app.services.retention import Retention, RetentionRun


@pytest.fixture
def quick() -> Retention:
    """The loop's day-long sleeps, made short."""
    return Retention(first_run_delay_seconds=0, interval_seconds=0.01)


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(settings, 'audit_retention_days', 30)
    monkeypatch.setattr(settings, 'service_event_retention_days', 0)
    monkeypatch.setattr(settings, 'producer_feed_retention_days', 0)


class _Rounds:
    """`run_once` standing in: the first round raises, the rest remove one audit event."""

    def __init__(self) -> None:
        self.count = 0
        self.third = asyncio.Event()

    async def __call__(self) -> RetentionRun:
        self.count += 1
        if self.count >= 3:
            self.third.set()
        if self.count == 1:
            raise RuntimeError('the database went away mid-round')
        return RetentionRun(audit_events=1)


async def test_a_round_that_raises_is_counted_and_the_next_one_runs(monkeypatch, quick, windows, caplog):
    rounds = _Rounds()
    monkeypatch.setattr(quick, 'run_once', rounds)
    failures_before = RETENTION_FAILURES._value.get()
    RETENTION_LAST_SUCCESS.set(0)

    with caplog.at_level(logging.INFO, logger=retention.__name__):
        quick.start()
        await asyncio.wait_for(rounds.third.wait(), 5)
        await quick.stop()

    assert RETENTION_FAILURES._value.get() == failures_before + 1
    assert RETENTION_LAST_SUCCESS._value.get() > 0
    assert 'Retention round failed; will try again' in caplog.text
    assert 'Retention removed' in caplog.text
    assert not quick.running


async def test_it_starts_with_every_window_at_zero(monkeypatch, caplog):
    """Exports and revocations have no 0 that keeps them: with the three
    windows at 0 the loop still has to run, or both stay for ever."""
    monkeypatch.setattr(settings, 'audit_retention_days', 0)
    monkeypatch.setattr(settings, 'service_event_retention_days', 0)
    monkeypatch.setattr(settings, 'producer_feed_retention_days', 0)
    idle = Retention(first_run_delay_seconds=3600)

    with caplog.at_level(logging.INFO, logger=retention.__name__):
        idle.start()

    assert idle.running
    assert 'expired producer exports and revocations are cleared each round' in caplog.text
    assert 'nothing will be deleted' not in caplog.text
    await idle.stop()
    assert not idle.running


async def test_stopping_waits_for_the_loop_and_says_when_it_had_died(monkeypatch, quick, windows, caplog):
    """A loop that ended of something other than the cancel took the
    advisory lock's session with it; shutdown carries on, and says so."""

    async def dies() -> None:
        raise RuntimeError('lost the session')

    monkeypatch.setattr(quick, '_loop', dies)
    quick.start()
    await asyncio.sleep(0)

    with caplog.at_level(logging.ERROR, logger=retention.__name__):
        await quick.stop()

    assert 'Retention loop did not unwind cleanly' in caplog.text
