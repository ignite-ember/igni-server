"""The deployment-wide rounds walk every organisation that is routed to, and pass over a deleted one.

Waking VMs, expiring events and export archives, handing out exports and
draining the feed each go through every organisation's own database in
turn. A deleted organisation is detached: its database is kept and
nothing routes to it, so a round that reached it would fail for every
organisation after it. These pin both halves — each live organisation
is reached, the deleted one is skipped — with three organisations, the
last of them deleted.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
import pytest

from app.api.base_v1.schema.vm_profiles import PlatformVmCapsBody
from app.config import settings
from app.db.sql.producer_feed import ProducerFeedDB
from app.db.sql.producer_feed_tenant import ProducerFeedTenantDB
from app.db.sql.tenant_registry import TenantRegistryDB
from app.services.cloud.producer_events import ProducerEvents
from app.services.cloud.producer_exports import ProducerExports
from app.services.cloud.producer_feed import ProducerFeed
from app.services.cloud.producer_states import ProducerStates
from app.services.cloud.vm_commands import VmCommands
from tests.cloud_world import CloudOrg, platform, shipped, token_for

pytestmark = pytest.mark.anyio

EXTENSION = 'igni.codeindex'
ARCHIVE = b'\x1f\x8b' + b'conversations' * 100


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    monkeypatch.setattr(settings, 'secret_encryption_key', Fernet.generate_key().decode())


@pytest.fixture
async def orgs(client) -> list[CloudOrg]:
    """Acme and Globex, which are live, and Initech, which will be deleted."""
    return [await CloudOrg(client, slug=slug).create() for slug in ('acme', 'globex', 'initech')]


def _later(monkeypatch, module: str, delta: timedelta) -> None:
    monkeypatch.setattr(
        f'{module}.datetime',
        type('Later', (), {'now': staticmethod(lambda tz=None: datetime.now(timezone.utc) + delta)}),
    )


async def test_a_command_wakes_its_vm_in_every_live_organisation(client, orgs):
    people = {}
    for org in orgs:
        person, headers = await org.person(f'ada@{org.slug}.com')
        response = await client.post(
            '/vm/commands', json={'producer': {'kind': 'person', 'id': person}, 'command': 'true'}, headers=headers
        )
        assert response.status_code == 202, response.text
        people[org.org_id] = person
    acme, globex, initech = orgs
    await TenantRegistryDB().detach(initech.org_id)

    wakes = await VmCommands().wakes()

    assert sorted((w.org_id, w.producer.id, w.reasons) for w in wakes) == sorted(
        (org.org_id, people[org.org_id], ['command']) for org in (acme, globex)
    )


class TestEvents:
    @pytest.fixture
    async def sent(self, client, orgs) -> dict[str, tuple[str, str, dict[str, str]]]:
        """An event waiting in each organisation: its person, its id and the service's token there."""
        service = await shipped(client, EXTENSION)
        sent = {}
        for org in orgs:
            group = await org.group('Legal')
            person, _ = await org.person(f'ada@{org.slug}.com', group)
            await org.switch_group(group, EXTENSION)
            token = await token_for(client, service, organisation=org.org_id)
            response = await client.post(
                '/service-events',
                json={'producer': {'kind': 'person', 'id': person}, 'type': 'index.ready', 'payload': {}},
                headers=token,
            )
            assert response.status_code == 202, response.text
            sent[org.org_id] = (person, response.json()['event_id'], token)
        await TenantRegistryDB().detach(orgs[2].org_id)
        return sent

    async def test_an_event_wakes_its_producer_in_every_live_organisation(self, orgs, sent):
        wakes = await ProducerEvents().wakes()

        assert sorted((w.org_id, w.producer.id, w.reasons) for w in wakes) == sorted(
            (org.org_id, sent[org.org_id][0], ['event']) for org in orgs[:2]
        )

    async def test_the_retention_round_expires_and_then_deletes_events_in_every_live_organisation(
        self, client, orgs, sent, monkeypatch
    ):
        _later(monkeypatch, 'app.db.sql.producer_events', timedelta(days=15))
        await ProducerEvents().close_out()
        _later(monkeypatch, 'app.db.sql.producer_events', timedelta(days=15 + 31))

        purged = await ProducerEvents().close_out()

        assert purged == 2
        for org in orgs[:2]:
            _, event_id, token = sent[org.org_id]
            assert (await client.get(f'/service-events/{event_id}', headers=token)).status_code == 404


class TestExports:
    @pytest.fixture
    async def gateway(self, client) -> dict[str, str]:
        return await platform(client)

    async def _removed(self, client, gateway, org: CloudOrg) -> tuple[str, str]:
        """A member removed from the organisation and kept for retention: their id and incarnation."""
        person, _ = await org.person(f'ada@{org.slug}.com')
        await client.get('/platform/producers', headers=gateway)
        await client.delete(f'/portal/team/{person}', headers=org.admin)
        producers = (await client.get('/platform/producers', headers=gateway)).json()['producers']
        [row] = [p for p in producers if p['org_id'] == org.org_id and p['producer']['id'] == person]
        return person, row['incarnation']

    async def _asked(self, client, org: CloudOrg, person: str) -> str:
        response = await client.post(
            f'/portal/orgs/{org.org_id}/producers/person/{person}/exports', json={}, headers=org.admin
        )
        assert response.status_code == 202, response.text
        return str(response.json()['export_id'])

    async def test_exports_waiting_in_every_live_organisation_are_handed_out(self, client, orgs, gateway):
        asked = {}
        for org in orgs:
            person, _ = await self._removed(client, gateway, org)
            asked[org.org_id] = await self._asked(client, org, person)
        await TenantRegistryDB().detach(orgs[2].org_id)

        pulled = (await client.post('/platform/exports/pull', headers=gateway)).json()['exports']

        assert sorted((e['org_id'], e['export_id']) for e in pulled) == sorted(
            (org.org_id, asked[org.org_id]) for org in orgs[:2]
        )

    async def test_an_export_of_an_incarnation_erased_meanwhile_fails_and_says_why(self, client, orgs, gateway):
        acme = orgs[0]
        person, _ = await self._removed(client, gateway, acme)
        export_id = await self._asked(client, acme, person)
        await client.put(
            f'/portal/orgs/{acme.org_id}/retention', json={'removed_member_retention_days': 0}, headers=acme.admin
        )
        await client.post('/portal/erasure', json={'email': f'ada@{acme.slug}.com'}, headers=acme.admin)

        pulled = await ProducerExports().pending(by_client_id='node-agent')

        read = (await client.get(f'/portal/orgs/{acme.org_id}/exports/{export_id}', headers=acme.admin)).json()
        assert pulled.exports == []
        assert (read['state'], read['error']) == ('failed', 'the incarnation was erased before it could be exported')

    async def test_the_retention_round_deletes_expired_archives_in_every_live_organisation(
        self, client, orgs, gateway, monkeypatch
    ):
        for org in orgs:
            person, _ = await self._removed(client, gateway, org)
            await self._asked(client, org, person)
        for export in (await client.post('/platform/exports/pull', headers=gateway)).json()['exports']:
            uploaded = await client.put(
                f'/platform/orgs/{export["org_id"]}/exports/{export["export_id"]}/archive',
                content=ARCHIVE,
                headers=gateway,
            )
            assert uploaded.status_code == 200, uploaded.text
        await TenantRegistryDB().detach(orgs[2].org_id)
        _later(monkeypatch, 'app.db.sql.producer_exports', timedelta(days=8))

        assert await ProducerExports().close_out() == 2


async def test_the_feed_drains_the_marks_of_every_live_organisation(client, orgs):
    groups = {org.org_id: await org.group('Legal') for org in orgs}
    await ProducerFeed().drain()
    before = max((row.seq for row in await ProducerFeedDB().after(0, limit=1000)), default=0)
    joined = {}
    for org in orgs:
        joined[org.org_id], _ = await org.person(f'bo@{org.slug}.com', groups[org.org_id])
    acme, globex, initech = orgs
    assert await ProducerFeedTenantDB().marks(globex.org_id) != []
    await TenantRegistryDB().detach(initech.org_id)

    await ProducerFeed().drain()

    assert [await ProducerFeedTenantDB().marks(org.org_id) for org in (acme, globex)] == [[], []]
    numbered = {row.producer_id for row in await ProducerFeedDB().after(before, limit=1000)}
    assert {joined[acme.org_id], joined[globex.org_id]} <= numbered


async def test_a_deleted_organisation_s_producers_all_read_as_removed(orgs):
    acme, _, initech = orgs
    person, _ = await initech.person('ada@initech.com')
    kept, _ = await acme.person('ada@acme.com')
    await TenantRegistryDB().database_of(initech.org_id)
    await TenantRegistryDB().detach(initech.org_id)
    caps = PlatformVmCapsBody(max_memory_mib=65536, max_vcpus=32)

    deleted = await ProducerStates(initech.org_id, caps=caps).of('person', [person])
    live = await ProducerStates(acme.org_id, caps=caps).of('person', [kept])

    assert (deleted.removed, deleted.states) == ({person}, [])
    assert (live.removed, [state.key.producer_id for state in live.states]) == (set(), [kept])
