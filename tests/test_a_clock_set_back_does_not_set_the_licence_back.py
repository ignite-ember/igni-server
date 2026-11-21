"""R519: winding the clock back past a licence's expiry does not make it valid again.

The licence is evaluated at `max(now, mark - 24 h, issued)`, where `mark`
is the latest time this deployment has seen. igni server holds the one
mark for the whole deployment in its encrypted integrity file
(`EncryptedIntegrityStore`), and it only moves on while this process's
clock and the database's agree within 48 hours (docs/licence-file.md,
"The clock"). With a correct clock nothing changes. Every clock here is
injected: the app's and the database's are moved by hand, separately, and
a "restart" is a fresh store on the same state directory.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path

import pytest

from app.db.sql.models.audit_event import DeploymentAuditEvent
from app.services.deployment_licence import (
    DAILY_ROUTE,
    deployment_licence,
    DeploymentLicence,
    EncryptedIntegrityStore,
    LicenceClockKeeper,
)
from app.services.licence import (
    clock as licence_clock,
    CLOCK_TOLERANCE,
    Entitlement,
    HighWaterMark,
    LicenceEnvelope,
    LicenceFile,
    LicenceState,
    Module,
    Operation,
)
from tests.conftest import auth_header, create_user, make_token
from tests.licence_kit import EXPIRES, FakeClock, payload, signed, verifier

#: The R519 moments: the licence read-only and seen so, then the clock wound back to before it expired.
READ_ONLY_AT = EXPIRES + timedelta(days=100)
BEFORE_EXPIRY = EXPIRES - timedelta(days=1)
ISSUED = EXPIRES - timedelta(days=365)


class AlwaysLock:
    """A stand-in advisory lock that always grants: in a test there is one writer."""

    def __init__(self, grant: bool = True) -> None:
        self._grant = grant

    async def try_acquire(self, key: int) -> bool:
        return self._grant

    async def release(self, key: int) -> None:
        return None


class StateOnItsOwnClock(EncryptedIntegrityStore):
    """The encrypted integrity store, with the database's clock a test moves by hand.

    `stored`/`advance` are the real file logic; only `database_now` is
    faked, and `seed` is read from the real test database (the audit trail)
    unless a test turns it off.
    """

    def __init__(self, directory: Path, database_clock: FakeClock, *, seed_from_db: bool = True) -> None:
        super().__init__(directory, locks=AlwaysLock())
        self._database_clock = database_clock
        self._seed_from_db = seed_from_db

    async def database_now(self) -> datetime:
        return self._database_clock.now()

    async def seed(self) -> datetime | None:
        return await super().seed() if self._seed_from_db else None


class Deployment:
    """One igni server process: its licence file, its mark in memory, the keeper on a state directory.

    A restart is a new `Deployment` on the same `state_dir`."""

    def __init__(
        self,
        path: Path,
        state_dir: Path,
        app_clock: FakeClock,
        database_clock: FakeClock,
        *,
        seed_from_db: bool = True,
    ) -> None:
        self.mark = HighWaterMark()
        self.source = LicenceFile(path, verifier=verifier(), clock=app_clock, high_water=self.mark)
        self.store = StateOnItsOwnClock(state_dir, database_clock, seed_from_db=seed_from_db)
        self.licence = DeploymentLicence(self.source, verifier=verifier(), store=self.store)
        self.keeper = LicenceClockKeeper(self.mark, store=self.store, clock=app_clock)

    def state(self) -> LicenceState:
        return self.source.status(Module.CODEINDEX).state


@pytest.fixture
def licence_path(tmp_path: Path) -> Path:
    path = tmp_path / 'licence' / 'licence.json'
    path.parent.mkdir(parents=True)
    path.write_bytes(signed(payload()))
    return path


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / 'state'


@pytest.fixture
def app_clock() -> FakeClock:
    return FakeClock(READ_ONLY_AT)


@pytest.fixture
def database_clock() -> FakeClock:
    return FakeClock(READ_ONLY_AT)


@pytest.fixture
def deployment(licence_path: Path, state_dir: Path, app_clock: FakeClock, database_clock: FakeClock) -> Deployment:
    return Deployment(licence_path, state_dir, app_clock, database_clock)


def _wind_back(*clocks: FakeClock, to: datetime = BEFORE_EXPIRY) -> None:
    for clock in clocks:
        clock.set(to)


async def test_a_licence_seen_read_only_stays_read_only_when_the_clock_is_wound_back(
    deployment, app_clock, database_clock
) -> None:
    await deployment.keeper.sync()
    assert deployment.state() is LicenceState.READ_ONLY

    _wind_back(app_clock, database_clock)

    status = deployment.source.status(Module.CODEINDEX)
    assert status.state is LicenceState.READ_ONLY
    assert not status.allows(Operation.WRITE)
    assert deployment.source.evaluated_at() == READ_ONLY_AT - CLOCK_TOLERANCE
    # Both clocks agree on the wound-back time, and the mark still does not go back.
    assert await deployment.keeper.sync() == READ_ONLY_AT
    assert deployment.state() is LicenceState.READ_ONLY


@pytest.mark.parametrize('database_wound_back_too', [True, False], ids=['both-clocks', 'app-clock-only'])
async def test_the_mark_outlives_a_restart(
    licence_path, state_dir, app_clock, database_clock, database_wound_back_too
) -> None:
    first = Deployment(licence_path, state_dir, app_clock, database_clock, seed_from_db=False)
    await first.keeper.sync()
    _wind_back(app_clock, *([database_clock] if database_wound_back_too else []))

    restarted = Deployment(licence_path, state_dir, app_clock, database_clock, seed_from_db=False)
    await restarted.keeper.sync()

    assert restarted.mark.seen == READ_ONLY_AT
    assert restarted.state() is LicenceState.READ_ONLY
    assert await restarted.store.stored() == READ_ONLY_AT


async def test_a_clock_within_a_day_of_the_mark_is_taken_as_it_is(deployment, app_clock, database_clock) -> None:
    app_clock.set(EXPIRES + timedelta(hours=12))
    database_clock.set(EXPIRES + timedelta(hours=12))
    await deployment.keeper.sync()
    assert deployment.state() is LicenceState.GRACE

    # NTP steps the clock back 23 hours: inside the tolerance, so the licence follows it, and nobody is told.
    _wind_back(app_clock, database_clock, to=EXPIRES - timedelta(hours=11))

    assert deployment.source.evaluated_at() == app_clock.now()
    assert deployment.state() is LicenceState.VALID
    assert deployment.licence.overview().clock is None


async def test_a_correct_clock_changes_nothing(deployment, app_clock, database_clock) -> None:
    for moment in (EXPIRES - timedelta(days=60), EXPIRES + timedelta(days=1), READ_ONLY_AT):
        _wind_back(app_clock, database_clock, to=moment)
        await deployment.keeper.sync()

        assert deployment.source.evaluated_at() == moment
        plain = LicenceFile(deployment.source.path, verifier=verifier(), clock=app_clock)
        assert deployment.source.status(Module.CODEINDEX) == plain.status(Module.CODEINDEX)
        assert deployment.licence.overview().clock is None


@pytest.mark.parametrize(
    'database_ahead', [timedelta(0), timedelta(days=3)], ids=['database-right', 'database-3-days-on']
)
async def test_a_wrong_future_clock_the_database_does_not_share_moves_nothing(
    licence_path, state_dir, app_clock, database_clock, database_ahead
) -> None:
    deployment = Deployment(licence_path, state_dir, app_clock, database_clock, seed_from_db=False)
    start = EXPIRES - timedelta(days=60)
    _wind_back(app_clock, database_clock, to=start)
    await deployment.keeper.sync()

    # Somebody types next year by mistake. The database's clock is right, or a little off itself: either way
    # the two are days apart, and neither time is written down.
    app_clock.set(READ_ONLY_AT)
    database_clock.set(start + database_ahead)
    await deployment.keeper.sync()

    assert await deployment.store.stored() == start
    assert deployment.mark.seen == start

    app_clock.set(start)  # corrected: nothing is left behind
    assert deployment.state() is LicenceState.VALID
    assert deployment.licence.overview().clock is None


async def test_two_clocks_three_days_apart_do_not_agree() -> None:
    now = READ_ONLY_AT
    assert HighWaterMark.agreed(now, now + timedelta(hours=47)) == now
    assert HighWaterMark.agreed(now + timedelta(hours=47), now) == now
    assert HighWaterMark.agreed(now, now + timedelta(hours=49)) is None
    assert HighWaterMark.agreed(now - timedelta(hours=49), now) is None


async def test_the_clock_warning_is_admin_only_and_reworded(
    client, db, app, deployment, app_clock, database_clock, caplog
) -> None:
    await deployment.keeper.sync()
    _wind_back(app_clock, database_clock)
    floor = (READ_ONLY_AT - CLOCK_TOLERANCE).astimezone(timezone.utc)
    expected = (
        'The system clock is behind a time this deployment has already seen. '
        f'The licence is evaluated as of no earlier than {floor:%Y-%m-%d %H:%M} UTC; fix the clock.'
    )

    with caplog.at_level(logging.WARNING, logger=licence_clock.__name__):
        await deployment.keeper.sync()

    assert [r.getMessage() for r in caplog.records if r.name == licence_clock.__name__] == [f'licence: {expected}']
    # The super admin sees it on the overview; the everybody notice has no clock field at all.
    assert deployment.licence.overview().clock == expected
    assert 'clock' not in deployment.licence.notice().model_dump()

    app.dependency_overrides[deployment_licence] = lambda: deployment.licence
    try:
        user = await create_user(db, email='member@example.com', is_super_admin=False)
        notice = (await client.get('/portal/licence/notice', headers=auth_header(make_token(str(user.user_id))))).json()
    finally:
        app.dependency_overrides.pop(deployment_licence, None)
    assert 'clock' not in notice
    assert notice['warnings'][0]['state'] == 'read_only'


async def test_nobody_is_told_about_the_clock_without_a_licence(tmp_path, app_clock, database_clock) -> None:
    bare = Deployment(tmp_path / 'none.json', tmp_path / 'state', app_clock, database_clock, seed_from_db=False)
    await bare.keeper.sync()
    _wind_back(app_clock, database_clock)

    assert bare.licence.overview().clock is None
    assert bare.licence.notice().warnings == []


async def _daily_record_at(db, at: datetime) -> None:
    db.add(
        DeploymentAuditEvent(
            occurred_at=at,
            method='SYSTEM',
            route=DAILY_ROUTE,
            status_code=200,
            outcome='success',
            target_type='licence',
            target_id='IE-TEST-0042',
        )
    )
    await db.commit()


async def test_the_first_start_takes_the_mark_from_the_daily_licence_record(
    db, deployment, app_clock, database_clock
) -> None:
    # An older release recorded the licence read-only (R519's audit row); this one starts with the clock wound back.
    await _daily_record_at(db, READ_ONLY_AT - timedelta(days=2))
    await _daily_record_at(db, READ_ONLY_AT)
    app_clock.set(BEFORE_EXPIRY)
    database_clock.set(READ_ONLY_AT + timedelta(hours=1))  # disagreeing: only the record can move the mark

    await deployment.keeper.sync()

    assert await deployment.store.stored() == READ_ONLY_AT
    assert deployment.state() is LicenceState.READ_ONLY


async def test_a_daily_record_from_beyond_the_databases_clock_is_not_believed(
    db, deployment, app_clock, database_clock
) -> None:
    # Written by a clock set into the future, which the database never shared.
    await _daily_record_at(db, READ_ONLY_AT)
    app_clock.set(BEFORE_EXPIRY)
    database_clock.set(READ_ONLY_AT - timedelta(days=3))

    await deployment.keeper.sync()

    assert await deployment.store.stored() is None
    assert deployment.state() is LicenceState.VALID


async def test_a_renewed_licence_applies_at_once_whatever_the_clock(
    deployment, licence_path, app_clock, database_clock
) -> None:
    await deployment.keeper.sync()
    _wind_back(app_clock, database_clock)
    assert deployment.state() is LicenceState.READ_ONLY

    renewed = payload({Module.CODEINDEX: Entitlement(users=100, expires=READ_ONLY_AT + timedelta(days=365))})
    deployment.licence.install(LicenceEnvelope.model_validate(json.loads(signed(renewed))))

    status = deployment.source.status(Module.CODEINDEX)
    assert (status.state, status.reason) == (LicenceState.VALID, None)
    assert status.allows(Operation.WRITE)


async def test_the_licence_is_never_evaluated_before_it_was_issued(deployment, app_clock) -> None:
    app_clock.set(ISSUED - timedelta(days=3650))

    assert deployment.source.evaluated_at() == ISSUED
    assert deployment.source.evaluated_at() == payload().issued


async def test_the_database_clock_is_the_databases(state_dir) -> None:
    database_now = await EncryptedIntegrityStore(state_dir).database_now()

    assert abs(database_now - datetime.now(timezone.utc)) < timedelta(minutes=5)


async def test_the_stored_mark_only_goes_up(state_dir, database_clock) -> None:
    store = StateOnItsOwnClock(state_dir, database_clock, seed_from_db=False)
    await store.load()

    assert await store.advance(READ_ONLY_AT) == READ_ONLY_AT
    assert await store.advance(BEFORE_EXPIRY) == READ_ONLY_AT
    assert await store.stored() == READ_ONLY_AT
