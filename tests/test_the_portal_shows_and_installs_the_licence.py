"""The portal's side of the licence: a banner for everybody, details and installing for a super admin.

igni server does not enforce the licence (the modules do, docs/licence-file.md);
these pin what it shows, that an upload is verified before it is
installed, that the base is untouched without one, and the daily audit
record while something needs attention.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
import json
from pathlib import Path

import pytest

from app.db.sql.advisory_locks import AdvisoryLockDB
from app.db.sql.audit_events import AuditEventDB
from app.services.deployment_licence import DAILY_ROUTE, DeploymentLicence, LicenceDailyAudit
from app.services.licence import Base64Url, Entitlement, Module
from tests.conftest import audit_rows, auth_header, create_user, make_token
from tests.licence_kit import EXPIRES, FakeClock, licence_file, payload, signed


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(EXPIRES - timedelta(days=100))


@pytest.fixture
def licence_path(tmp_path: Path, clock: FakeClock) -> Iterator[Path]:
    path = tmp_path / 'licence' / 'licence.json'
    DeploymentLicence.use(DeploymentLicence(licence_file(path, clock)))
    yield path
    DeploymentLicence.use(None)


async def _person(db, *, super_admin: bool) -> dict[str, str]:
    email = 'owner@example.com' if super_admin else 'member@example.com'
    user = await create_user(db, email=email, is_super_admin=super_admin)
    return auth_header(make_token(str(user.user_id)))


async def test_without_a_licence_there_is_no_banner_and_the_base_works(client, db, licence_path) -> None:
    person = await _person(db, super_admin=False)

    assert (await client.get('/portal/licence/notice', headers=person)).json() == {'warnings': []}
    assert (await client.get('/portal/me', headers=person)).status_code == 200


async def test_a_super_admin_installs_a_licence_and_sees_each_module(client, db, licence_path) -> None:
    admin = await _person(db, super_admin=True)
    envelope = json.loads(signed(payload()))

    response = await client.put('/portal/licence', json=envelope, headers=admin)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body['installed'], body['licence_id'], body['customer'], body['kind']) == (
        True,
        'IE-TEST-0042',
        'Example Bank N.V.',
        'commercial',
    )
    states = {module['module']: (module['state'], module['reason']) for module in body['modules']}
    assert states == {
        'codeindex': ('valid', None),
        'artifacts': ('unlicensed', 'not_licensed'),
        'fleet': ('unlicensed', 'not_licensed'),
        'factory': ('unlicensed', 'not_licensed'),
        'autopilot': ('unlicensed', 'not_licensed'),
    }
    assert json.loads(licence_path.read_bytes()) == envelope
    assert audit_rows(route='/v1/portal/licence')[0]['method'] == 'PUT'


async def test_an_altered_licence_is_refused_and_nothing_is_installed(client, db, licence_path) -> None:
    admin = await _person(db, super_admin=True)
    envelope = json.loads(signed(payload()))
    body = json.loads(Base64Url.decode(envelope['payload']))
    body['modules']['codeindex']['users'] = 1000
    envelope['payload'] = Base64Url.encode(json.dumps(body).encode())

    response = await client.put('/portal/licence', json=envelope, headers=admin)

    assert response.status_code == 422
    assert response.json()['detail'] == {
        'reason': 'invalid_signature',
        'message': 'This file is not a licence igni accepts (invalid_signature).',
    }
    assert not licence_path.exists()


async def test_only_a_super_admin_sees_or_installs_the_licence(client, db, licence_path) -> None:
    member = await _person(db, super_admin=False)

    assert (await client.get('/portal/licence', headers=member)).status_code == 403
    assert (await client.put('/portal/licence', json=json.loads(signed(payload())), headers=member)).status_code == 403
    assert not licence_path.exists()


async def test_everybody_sees_the_banner_while_a_licence_has_lapsed(client, db, licence_path, clock) -> None:
    licence_path.parent.mkdir(parents=True)
    licence_path.write_bytes(
        signed(
            payload(
                {
                    Module.CODEINDEX: Entitlement(users=100, expires=EXPIRES),
                    Module.FACTORY: Entitlement(expires=EXPIRES + timedelta(days=365)),
                }
            )
        )
    )
    clock.set(EXPIRES + timedelta(days=10))
    member = await _person(db, super_admin=False)

    notice = (await client.get('/portal/licence/notice', headers=member)).json()

    assert notice == {
        'warnings': [
            {
                'module': 'codeindex',
                'state': 'grace',
                'message': 'The licence for CodeIndex expired on 29 October 2027. CodeIndex keeps working until '
                '27 January 2028 (in 80 days), then becomes read-only. Install a renewed licence file to continue.',
            }
        ]
    }


async def test_the_audit_trail_records_a_lapsed_licence_once_a_day(licence_path, clock) -> None:
    licence_path.parent.mkdir(parents=True)
    licence_path.write_bytes(signed(payload()))
    clock.set(EXPIRES + timedelta(days=200))
    # The licence runs on the test's clock; the day is the audit trail's own (real) one.
    job = LicenceDailyAudit(audit=AuditEventDB(), locks=AdvisoryLockDB())

    assert await job.run_once() is True
    assert await job.run_once() is False

    rows = audit_rows(route=DAILY_ROUTE)
    assert len(rows) == 1
    assert (rows[0]['method'], rows[0]['target_type'], rows[0]['target_id'], rows[0]['org_id']) == (
        'SYSTEM',
        'licence',
        'IE-TEST-0042',
        None,
    )
    assert rows[0]['detail']['modules'][0]['state'] == 'read_only'


async def test_no_audit_record_while_nothing_needs_attention(licence_path, clock) -> None:
    licence_path.parent.mkdir(parents=True)
    licence_path.write_bytes(signed(payload()))

    assert await LicenceDailyAudit().run_once() is False
    assert audit_rows(route=DAILY_ROUTE) == []
