"""An org admin exports a removed member's data during retention; the node agent runs igni's export.

igni server queues the export; the node agent pulls it, runs
`igni-cloud db export-producer` for the incarnation and uploads the
archive; the org admin downloads it until it expires. An incarnation
already erased cannot be exported (`docs/service-identity.md` §18.4).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import retention
from tests.cloud_world import CloudOrg, platform
from tests.conftest import audit_rows

pytestmark = pytest.mark.anyio

ARCHIVE = b'\x1f\x8b' + b'conversations' * 1000


@pytest.fixture
async def world(client):
    acme = await CloudOrg(client).create()
    gateway = await platform(client)
    ada, _ = await acme.person('ada@acme.com')
    await client.get('/platform/producers', headers=gateway)
    await client.delete(f'/portal/team/{ada}', headers=acme.admin)
    [row] = (await client.get('/platform/producers', headers=gateway)).json()['producers'][-1:]
    return {'acme': acme, 'gateway': gateway, 'ada': ada, 'incarnation': row['incarnation']}


def _exports(world) -> str:
    return f'/portal/orgs/{world["acme"].org_id}/producers/person/{world["ada"]}/exports'


async def test_requested_run_by_the_node_agent_and_downloaded(client, world):
    acme, gateway = world['acme'], world['gateway']

    asked = await client.post(_exports(world), json={}, headers=acme.admin)
    pulled = (await client.post('/platform/exports/pull', headers=gateway)).json()['exports']
    again = (await client.post('/platform/exports/pull', headers=gateway)).json()['exports']
    export_id = asked.json()['export_id']
    uploaded = await client.put(
        f'/platform/orgs/{acme.org_id}/exports/{export_id}/archive', content=ARCHIVE, headers=gateway
    )
    downloaded = await client.get(f'/portal/orgs/{acme.org_id}/exports/{export_id}/download', headers=acme.admin)
    listed = (await client.get(f'/portal/orgs/{acme.org_id}/exports', headers=acme.admin)).json()['exports']

    assert asked.status_code == 202, asked.text
    assert (asked.json()['state'], asked.json()['incarnation']) == ('requested', world['incarnation'])
    assert pulled == [
        {
            'export_id': export_id,
            'org_id': acme.org_id,
            'producer': {'kind': 'person', 'id': world['ada']},
            'incarnation': world['incarnation'],
            'command': [
                'igni-cloud',
                'db',
                'export-producer',
                '--org',
                acme.org_id,
                '--producer',
                f'person:{world["ada"]}',
                '--incarnation',
                world['incarnation'],
            ],
        }
    ]
    assert again == []
    assert uploaded.status_code == 200, uploaded.text
    assert (uploaded.json()['state'], uploaded.json()['size']) == ('ready', len(ARCHIVE))
    expires = datetime.fromisoformat(uploaded.json()['expires_at'])
    assert timedelta(days=6, hours=23) < expires - datetime.now(timezone.utc) <= timedelta(days=7)
    assert downloaded.status_code == 200
    assert downloaded.content == ARCHIVE
    assert [e['state'] for e in listed] == ['ready']


async def test_a_failed_run_says_why(client, world):
    acme, gateway = world['acme'], world['gateway']
    export_id = (await client.post(_exports(world), json={}, headers=acme.admin)).json()['export_id']
    await client.post('/platform/exports/pull', headers=gateway)

    failed = await client.post(
        f'/platform/orgs/{acme.org_id}/exports/{export_id}/failed', json={'error': 'database gone'}, headers=gateway
    )
    read = (await client.get(f'/portal/orgs/{acme.org_id}/exports/{export_id}', headers=acme.admin)).json()
    early = await client.get(f'/portal/orgs/{acme.org_id}/exports/{export_id}/download', headers=acme.admin)

    assert failed.status_code == 204
    assert (read['state'], read['error']) == ('failed', 'database gone')
    assert early.status_code == 409


async def test_nothing_to_export_once_erased_or_for_a_member(client, world):
    acme = world['acme']
    max_id, _ = await acme.person('max@acme.com')
    await client.put(
        f'/portal/orgs/{acme.org_id}/retention', json={'removed_member_retention_days': 0}, headers=acme.admin
    )
    await client.post('/portal/erasure', json={'email': 'ada@acme.com'}, headers=acme.admin)

    erased = await client.post(_exports(world), json={}, headers=acme.admin)
    member = await client.post(
        f'/portal/orgs/{acme.org_id}/producers/person/{max_id}/exports', json={}, headers=acme.admin
    )

    assert erased.status_code == 404
    assert member.status_code == 404


async def test_only_admins_and_only_the_platform_runs_it(client, world):
    acme = world['acme']
    _, member = await acme.person('max@acme.com')

    assert (await client.post(_exports(world), json={}, headers=member)).status_code == 403
    assert (await client.post('/platform/exports/pull', headers=acme.admin)).status_code == 401


async def test_an_archive_expires(client, world, monkeypatch):
    acme, gateway = world['acme'], world['gateway']
    export_id = (await client.post(_exports(world), json={}, headers=acme.admin)).json()['export_id']
    await client.post('/platform/exports/pull', headers=gateway)
    await client.put(f'/platform/orgs/{acme.org_id}/exports/{export_id}/archive', content=ARCHIVE, headers=gateway)
    ahead = datetime.now(timezone.utc) + timedelta(days=8)
    monkeypatch.setattr(
        'app.db.sql.producer_exports.datetime', type('Later', (), {'now': staticmethod(lambda tz=None: ahead)})
    )

    removed = await retention.run_once()

    assert removed.producer_exports == 1
    assert (await client.get(f'/portal/orgs/{acme.org_id}/exports/{export_id}', headers=acme.admin)).status_code == 404


async def test_every_step_is_audited(client, world):
    acme, gateway = world['acme'], world['gateway']
    export_id = (await client.post(_exports(world), json={}, headers=acme.admin)).json()['export_id']
    await client.post('/platform/exports/pull', headers=gateway)
    await client.put(f'/platform/orgs/{acme.org_id}/exports/{export_id}/archive', content=ARCHIVE, headers=gateway)
    await client.get(f'/portal/orgs/{acme.org_id}/exports/{export_id}/download', headers=acme.admin)

    routes = [row['route'] for row in audit_rows()]

    assert any(route.endswith('/exports') for route in routes)
    assert any(route.endswith('/exports/pull') for route in routes)
    assert any(route.endswith('/archive') for route in routes)
    assert any(route.endswith('/download') for route in routes)
