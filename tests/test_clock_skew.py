"""Two clocks decide whether a withdrawn session is really withdrawn.

B8. B5's revocation compares a token's `iat` — stamped by whichever API
replica minted it — against `users.tokens_valid_after`. Both were app
clocks, so the comparison depended on two arbitrary replicas agreeing,
and nothing measured whether they did. Demonstrated against the real
code before the fix: a replica five seconds ahead minted a token with
`iat` 1788509183, a revocation from a replica in step wrote a watermark
of 1788509179, and the token **survived**.

Two changes, and neither is a wider margin:

* the watermark comes from Postgres now, so there is one gap — app clock
  against database clock — rather than one per pair of replicas;
* that gap is measured every minute, published as
  `igni_clock_skew_seconds`, warned about above one second and alerted
  on by `IgniClockSkew`.

**Why not simply widen the margin.** The self-service route signs the
caller out and returns them to sign-in, so a margin large enough to
cover plausible skew — five seconds, say — refuses the token a person
gets from an OAuth round trip taken immediately afterwards. A margin big
enough to hide skew is big enough to break the flow it protects. One
second, and make the skew visible.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import pytest

from app.monitoring import clock_skew
from app.monitoring.clock_skew import ClockSkew
from app.monitoring.metrics import CLOCK_SKEW

pytestmark = pytest.mark.anyio


class TestMeasuringIt:
    async def test_it_reports_a_small_number_against_a_live_database(self):
        """Same host in the test environment, so the honest expectation is
        "near zero" — and a measurement that cannot come back near zero
        would make the alert threshold meaningless."""
        skew = await ClockSkew().measure()
        assert skew is not None, 'the skew could not be measured at all'
        assert abs(skew) < 5.0, f'{skew}s against the test database, which shares this clock'

    async def test_it_publishes_the_gauge(self):
        """The gauge is what an operator sees; measuring without setting
        it would leave the alert permanently absent rather than firing."""
        CLOCK_SKEW.set(9999.0)
        skew = await ClockSkew().sample_once()
        assert skew is not None
        assert CLOCK_SKEW._value.get() == pytest.approx(skew)  # noqa: SLF001

    async def test_it_returns_none_rather_than_raising_when_it_cannot_ask(self, monkeypatch):
        """A metric must never be the thing that breaks a request or a
        startup. The loop treats None as "nothing to publish"."""

        def explode():
            raise RuntimeError('no database')

        monkeypatch.setattr('app.db.sql.SQLAlchemySessionManager.session_scope', lambda self: explode())
        assert await ClockSkew().measure() is None
        assert await ClockSkew().sample_once() is None

    async def test_the_threshold_is_the_revocation_margin(self):
        """Not a preference. `withdraw_sessions` rounds the watermark up
        by one second, so one second is exactly where skew stops being
        cosmetic — if that margin ever changes, this has to change with
        it, and this assertion is where somebody finds out.
        """
        assert clock_skew.WARN_SECONDS == 1.0

    async def test_a_large_skew_is_logged_with_what_it_breaks(self, monkeypatch, caplog):
        """The log line is the only thing a single-instance deployment
        without Prometheus will ever see, so it has to name the
        consequence rather than the number alone."""
        monkeypatch.setattr(ClockSkew, 'measure', lambda self: _resolved(4.2))

        with caplog.at_level('WARNING'):
            assert await ClockSkew().sample_once() == 4.2

        messages = ' '.join(r.getMessage() for r in caplog.records)
        assert 'Clock skew' in messages
        assert 'withdrawal' in messages, 'the warning does not say what the skew breaks'
        assert 'NTP' in messages, 'the warning does not say what to do'


async def _resolved(value: float) -> float:
    return value


class TestTheWatermarkComesFromTheDatabase:
    async def test_it_ignores_this_processs_clock(self, client, db, monkeypatch):
        """The fix, asserted where it can be seen.

        The app clock is moved an hour into the future. A watermark taken
        from `datetime.now()` would land an hour out; one taken from
        Postgres stays where the database is. This is the whole reason
        the statement is SQL rather than a Python value bound into it.
        """
        from app.db.sql.account_sessions import AccountSessionsDB
        from app.db.sql.user import UserDB

        user_id = f'skew-{uuid.uuid4().hex[:8]}'
        user_db = UserDB()
        await user_db.ensure_exists(user_id=user_id, email=f'{user_id}@example.invalid', full_name='Skewed')
        try:
            real_now = datetime.now(timezone.utc)

            class _AheadByAnHour(datetime):
                @classmethod
                def now(cls, tz=None):  # type: ignore[override]
                    return real_now + timedelta(hours=1)

            monkeypatch.setattr('app.db.sql.account_sessions.datetime', _AheadByAnHour)

            watermark = await AccountSessionsDB().withdraw_sessions(user_id)

            drift = abs((watermark - real_now).total_seconds())
            assert drift < 60, (
                f'the watermark is {drift:.0f}s from real time, so it came from this '
                "process's clock rather than the database's"
            )
        finally:
            await user_db.delete(user_id)

    async def test_it_is_still_rounded_up_to_a_whole_second(self, client, db):
        """The rounding moved into SQL with the clock, and it is what
        closes B5's same-second window. Moving a mechanism is the
        classic way to lose it."""
        from app.db.sql.account_sessions import AccountSessionsDB
        from app.db.sql.user import UserDB

        user_id = f'round-{uuid.uuid4().hex[:8]}'
        user_db = UserDB()
        await user_db.ensure_exists(user_id=user_id, email=f'{user_id}@example.invalid', full_name='Rounded')
        try:
            before = datetime.now(timezone.utc)
            watermark = await AccountSessionsDB().withdraw_sessions(user_id)
            assert watermark.microsecond == 0, f'the watermark is sub-second: {watermark!r}'
            assert watermark > before, 'the watermark is not ahead of the request'
        finally:
            await user_db.delete(user_id)
