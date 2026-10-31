"""The lapse sequence, on a fake clock: valid → grace (90 days) → read-only, never off.

What each state allows is the contract every paid module enforces
(docs/licence-file.md, "What each state allows"): read-only still reads
and exports, and nothing that was paid for is ever switched off.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from app.services.licence import (
    Entitlement,
    LicenceKind,
    LicenceReading,
    LicenceState,
    LicenceStatus,
    Module,
    Operation,
    Reason,
)
from app.services.licence.source import RECHECK
from tests.licence_kit import EXPIRES, FakeClock, licence_file, payload, signed

ALL = {Operation.ALWAYS, Operation.READ, Operation.WRITE}


def _allowed(status) -> set[Operation]:
    return {operation for operation in Operation if status.allows(operation)}


def test_a_commercial_licence_runs_its_whole_course(tmp_path: Path) -> None:
    path = tmp_path / 'licence.json'
    path.write_bytes(signed(payload()))
    clock = FakeClock(EXPIRES - timedelta(days=60))
    source = licence_file(path, clock)

    status = source.status(Module.CODEINDEX)
    assert (status.state, status.reason, status.message, status.days_left) == (LicenceState.VALID, None, None, 60)
    assert _allowed(status) == ALL

    clock.set(EXPIRES - timedelta(days=12))
    status = source.status(Module.CODEINDEX)
    assert (status.state, status.reason, status.days_left) == (LicenceState.VALID, Reason.EXPIRING, 12)
    assert status.message == (
        'The licence for CodeIndex expires on 29 October 2027 (in 12 days). '
        'Install the renewed licence file before then.'
    )

    clock.set(EXPIRES + timedelta(days=1))
    status = source.status(Module.CODEINDEX)
    assert (status.state, status.reason, status.days_left) == (LicenceState.GRACE, Reason.LAPSED, 89)
    assert _allowed(status) == ALL
    assert status.message == (
        'The licence for CodeIndex expired on 29 October 2027. CodeIndex keeps working until 27 January 2028 '
        '(in 89 days), then becomes read-only. Install a renewed licence file to continue.'
    )

    clock.set(EXPIRES + timedelta(days=90) - timedelta(seconds=1))
    assert source.status(Module.CODEINDEX).state is LicenceState.GRACE

    clock.set(EXPIRES + timedelta(days=90))
    status = source.status(Module.CODEINDEX)
    assert (status.state, status.reason) == (LicenceState.READ_ONLY, Reason.READ_ONLY)
    assert _allowed(status) == {Operation.ALWAYS, Operation.READ}

    clock.set(EXPIRES + timedelta(days=3650))
    assert source.status(Module.CODEINDEX).state is LicenceState.READ_ONLY


def test_read_only_still_reads_and_exports_and_refuses_changes() -> None:
    reading = LicenceReading(payload=payload())
    status = LicenceStatus.of(reading, Module.CODEINDEX, now=EXPIRES + timedelta(days=200))

    assert status.allows(Operation.READ)  # reads, searches, downloads and exports are all reads
    assert status.allows(Operation.ALWAYS)
    assert not status.allows(Operation.WRITE)
    assert status.message is not None and 'read and export everything' in status.message


def test_an_evaluation_goes_read_only_for_30_days_then_stops() -> None:
    # A kill switch at expiry is a finding a CISO will not accept, so an
    # ended evaluation keeps reads and exports for 30 days, then stops.
    licence = payload({Module.ARTIFACTS: Entitlement(users=25, expires=EXPIRES)}, kind=LicenceKind.EVALUATION)

    def at(delta: timedelta) -> LicenceStatus:
        return LicenceStatus.of(LicenceReading(payload=licence), Module.ARTIFACTS, now=EXPIRES + delta)

    before, ended, last_day, stopped = (
        at(-timedelta(days=3)),
        at(timedelta(seconds=1)),
        at(timedelta(days=30) - timedelta(seconds=1)),
        at(timedelta(days=30)),
    )

    assert (before.state, before.reason) == (LicenceState.VALID, Reason.EXPIRING)
    assert (ended.state, ended.reason) == (LicenceState.READ_ONLY, Reason.EVALUATION_EXPIRED)
    assert _allowed(ended) == {Operation.ALWAYS, Operation.READ}
    assert ended.read_only_from == EXPIRES
    assert ended.message is not None and 'read and export everything' in ended.message
    assert last_day.state is LicenceState.READ_ONLY
    assert (stopped.state, stopped.reason) == (LicenceState.UNLICENSED, Reason.EVALUATION_EXPIRED)
    assert _allowed(stopped) == {Operation.ALWAYS}


@pytest.mark.parametrize('reason', [Reason.NO_LICENCE, Reason.INVALID_SIGNATURE, Reason.UNKNOWN_KEY, Reason.MALFORMED])
def test_without_a_good_licence_a_module_is_not_enabled(reason: Reason) -> None:
    status = LicenceStatus.of(LicenceReading(problem=reason), Module.CODEINDEX, now=EXPIRES)

    assert (status.state, status.reason) == (LicenceState.UNLICENSED, reason)
    assert _allowed(status) == {Operation.ALWAYS}


def test_a_module_the_licence_does_not_name_is_not_enabled() -> None:
    status = LicenceStatus.of(LicenceReading(payload=payload()), Module.ARTIFACTS, now=EXPIRES - timedelta(days=100))

    assert (status.state, status.reason) == (LicenceState.UNLICENSED, Reason.NOT_LICENSED)


def test_autopilot_without_the_factory_is_refused() -> None:
    # The issuing tool refuses this licence; a hand-made one is refused here too.
    licence = payload({Module.AUTOPILOT: Entitlement(users=50, expires=EXPIRES)})

    status = LicenceStatus.of(LicenceReading(payload=licence), Module.AUTOPILOT, now=EXPIRES - timedelta(days=100))

    assert (status.state, status.reason) == (LicenceState.UNLICENSED, Reason.REQUIRES_FACTORY)
    assert status.message == (
        'Autopilot is not licensed on this deployment. '
        'Autopilot needs the Factory, and the installed licence does not include it.'
    )


def test_autopilot_is_never_better_off_than_the_factory() -> None:
    licence = payload(
        {
            Module.FACTORY: Entitlement(expires=EXPIRES),
            Module.AUTOPILOT: Entitlement(users=50, expires=EXPIRES + timedelta(days=365)),
        }
    )
    reading = LicenceReading(payload=licence)

    during = LicenceStatus.of(reading, Module.AUTOPILOT, now=EXPIRES - timedelta(days=100))
    lapsed = LicenceStatus.of(reading, Module.AUTOPILOT, now=EXPIRES + timedelta(days=100))

    assert during.state is LicenceState.VALID
    assert (lapsed.state, lapsed.reason, lapsed.users) == (LicenceState.READ_ONLY, Reason.READ_ONLY, 50)
    assert lapsed.message is not None and lapsed.message.startswith('Autopilot runs on the Factory. ')


def test_a_renewed_file_is_picked_up_without_a_restart(tmp_path: Path) -> None:
    path = tmp_path / 'licence.json'
    path.write_bytes(signed(payload()))
    clock = FakeClock(EXPIRES + timedelta(days=100))
    source = licence_file(path, clock)
    assert source.status(Module.CODEINDEX).state is LicenceState.READ_ONLY

    renewed = {Module.CODEINDEX: Entitlement(users=200, expires=EXPIRES + timedelta(days=365))}
    path.write_bytes(signed(payload(renewed, licence_id='IE-TEST-0043')))
    clock.advance(RECHECK)

    status = source.status(Module.CODEINDEX)
    assert (status.state, status.licence_id, status.users) == (LicenceState.VALID, 'IE-TEST-0043', 200)


def test_a_licence_file_that_is_removed_or_tampered_with_on_disk_stops_counting(tmp_path: Path) -> None:
    path = tmp_path / 'licence.json'
    path.write_bytes(signed(payload()))
    clock = FakeClock(EXPIRES - timedelta(days=100))
    source = licence_file(path, clock)
    assert source.status(Module.CODEINDEX).state is LicenceState.VALID

    path.write_bytes(path.read_bytes().replace(b'"signature": "', b'"signature": "A'))
    clock.advance(RECHECK)
    assert source.status(Module.CODEINDEX).reason is Reason.INVALID_SIGNATURE

    path.unlink()
    clock.advance(RECHECK)
    assert source.status(Module.CODEINDEX).reason is Reason.NO_LICENCE
