"""Every way a producer's state can change reaches the gateway's feed as it happens.

The feed numbers only what triggers marked (`docs/service-identity.md`
§11.3). Each test here makes one kind of change through the API, lets
the feed number its marks, then reconciles — works every producer out
afresh. A reconcile that finds anything to number is a change no mark
named: a path that escaped the feed.
"""

from __future__ import annotations

from cryptography.fernet import Fernet
import pytest

from app.config import settings
from app.db.sql.producer_feed import ProducerFeedDB
from app.services.cloud.producer_feed import ProducerFeed
from tests.cloud_world import CloudOrg, make_super_admin
from tests.conftest import auth_header, make_token

pytestmark = pytest.mark.anyio

BROWSER = {
    'name': 'Browser',
    'memory_mib': 4096,
    'vcpus': 2,
    'idle_before_pause_s': 600,
    'packages': {'apt': ['chromium'], 'pip': [], 'npm': []},
    'setup_script': None,
}


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    monkeypatch.setattr(settings, 'secret_encryption_key', Fernet.generate_key().decode())


class World:
    def __init__(self, client, acme: CloudOrg) -> None:
        self.client = client
        self.acme = acme
        self.legal = ''
        self.ada = ''
        self.max = ''
        self.app = ''
        self.registered = None
        self.browser = ''

    async def build(self) -> World:
        acme = self.acme
        self.legal = await acme.group('Legal')
        self.ada, _ = await acme.person('ada@acme.com', self.legal)
        self.max, _ = await acme.person('max@acme.com')
        self.registered = await acme.app()
        self.app = self.registered.client_id
        created = await self.client.post(f'/portal/orgs/{acme.org_id}/vm-profiles', json=BROWSER, headers=acme.admin)
        assert created.status_code == 201, created.text
        self.browser = created.json()['id']
        await ProducerFeed().drain()
        assert await ProducerFeed().reconcile() == 0
        return self

    async def call(self, method: str, path: str, **kwargs) -> None:
        response = await self.client.request(method, path, headers=self.acme.admin, **kwargs)
        assert response.status_code < 300, response.text

    def profiles(self, rest: str = '') -> str:
        return f'/portal/orgs/{self.acme.org_id}/vm-profiles{rest}'


@pytest.fixture
async def world(client) -> World:
    return await World(client, await CloudOrg(client).create()).build()


async def _escaped() -> int:
    """Number the marks — there must have been a change to number — then count what a reconcile still finds."""
    assert await ProducerFeed().drain() > 0, 'the change changed no producer: the test proves nothing'
    return await ProducerFeed().reconcile()


async def _escaped_a_removal(user_id: str) -> int:
    """A removal is numbered by the request that made it (`ProducerFeed.removal`), so nothing is left to number.

    The person's row must say so — removed — or the test proves nothing.
    """
    assert await ProducerFeed().drain() == 0, 'the removal left marks for a read to number'
    rows = [row for row in await ProducerFeedDB().after(0, limit=1000) if row.producer_id == user_id]
    assert [row.removed for row in rows] == [True]
    return await ProducerFeed().reconcile()


async def test_joining(world):
    await world.acme.person('eve@acme.com')
    assert await _escaped() == 0


async def test_deactivation_and_reactivation(world):
    await world.call('PATCH', f'/portal/team/{world.max}/status', json={'status': 'deactivated'})
    assert await _escaped() == 0
    await world.call('PATCH', f'/portal/team/{world.max}/status', json={'status': 'active'})
    assert await _escaped() == 0


async def test_removal(world):
    await world.call('DELETE', f'/portal/team/{world.max}')
    assert await _escaped_a_removal(world.max) == 0


async def test_a_group_move(world):
    await world.call(
        'PUT', f'/portal/orgs/{world.acme.org_id}/groups/{world.legal}/vm-profile', json={'profile_id': world.browser}
    )
    assert await _escaped() == 0
    await world.call('POST', f'/portal/groups/{world.legal}/members', json={'user_id': world.max})
    assert await _escaped() == 0


async def test_the_default_groups_profile(world):
    groups = (await world.client.get('/portal/groups', headers=world.acme.admin)).json()['groups']
    default = next(g['id'] for g in groups if g['is_default'])
    await world.call(
        'PUT', f'/portal/orgs/{world.acme.org_id}/groups/{default}/vm-profile', json={'profile_id': world.browser}
    )
    assert await _escaped() == 0


async def test_deleting_a_group(world):
    await world.call(
        'PUT', f'/portal/orgs/{world.acme.org_id}/groups/{world.legal}/vm-profile', json={'profile_id': world.browser}
    )
    await _escaped()
    await world.call('DELETE', f'/portal/groups/{world.legal}')
    assert await _escaped() == 0


async def test_editing_and_deleting_a_profile(world):
    await world.call(
        'PUT', f'/portal/orgs/{world.acme.org_id}/apps/{world.app}/vm-profile', json={'profile_id': world.browser}
    )
    assert await _escaped() == 0
    await world.call('PUT', world.profiles(f'/{world.browser}'), json={**BROWSER, 'memory_mib': 2048})
    assert await _escaped() == 0
    await world.call('DELETE', world.profiles(f'/{world.browser}'))
    assert await _escaped() == 0


async def test_an_apps_life(world):
    await world.call('POST', f'/portal/apps/{world.app}/vm-credential')
    assert await _escaped() == 0
    [credential] = (await world.client.get('/portal/vm-credentials', headers=world.acme.admin)).json()['credentials']
    await world.call('DELETE', f'/portal/vm-credentials/{credential["credential_id"]}')
    assert await _escaped() == 0
    await world.call('POST', f'/portal/apps/{world.app}/vm-credential')
    await _escaped()
    await world.call('DELETE', f'/portal/apps/{world.app}')
    # Deleting an app is discarding it, which numbers its removal itself (§18.5): nothing is left to drain.
    assert await ProducerFeed().reconcile() == 0


async def test_lowering_the_caps(world):
    await world.call(
        'PUT', f'/portal/orgs/{world.acme.org_id}/apps/{world.app}/vm-profile', json={'profile_id': world.browser}
    )
    await _escaped()
    await make_super_admin(world.acme.admin_id)
    await world.call(
        'PUT', '/portal/vm-caps', json={'max_memory_mib': 2048, 'max_vcpus': 1, 'max_idle_before_pause_s': 60}
    )
    assert await _escaped() == 0


async def test_deleting_an_account(world):
    response = await world.client.delete('/portal/me', headers=auth_header(make_token(world.max)))
    assert response.status_code == 200, response.text
    assert await _escaped_a_removal(world.max) == 0


async def test_deleting_the_organisation(world):
    """Deactivated members may stay until the end; the organisation's deletion takes them with it.

    Their removal is numbered by the deletion itself (`ProducerFeed.removal`),
    like any other: before, it waited for some later read to number it.
    """
    await world.call('PATCH', f'/portal/team/{world.ada}/status', json={'status': 'deactivated'})
    await world.call('DELETE', f'/portal/team/{world.max}')
    await world.call('DELETE', f'/portal/apps/{world.app}')
    await ProducerFeed().drain()
    await world.call('DELETE', f'/portal/organizations/{world.acme.org_id}')
    assert await _escaped_a_removal(world.ada) == 0


async def test_a_spaces_life(world):
    app = await world.acme.app_token(world.registered)
    for name in ('a', 'b'):
        response = await world.client.put(f'/apps/{world.app}/spaces/{name}', headers=app)
        assert response.status_code == 201, response.text
    assert await _escaped() == 0
    await world.call(
        'PUT', f'/portal/orgs/{world.acme.org_id}/apps/{world.app}/vm-profile', json={'profile_id': world.browser}
    )
    assert await _escaped() == 0
    assert (await world.client.delete(f'/apps/{world.app}/spaces/a', headers=app)).status_code == 204
    assert await _escaped() == 0
    await world.call('DELETE', f'/portal/apps/{world.app}')
    # Discarding the app numbers its and its spaces' removal itself (§18.5).
    assert await ProducerFeed().reconcile() == 0


async def test_growing_a_volume(world):
    await world.call('POST', f'/portal/orgs/{world.acme.org_id}/producers/person/{world.ada}/volume', json={'gib': 20})
    assert await _escaped() == 0


async def test_a_redeploy_that_changes_the_deployments_caps(world, monkeypatch):
    """No trigger sees a setting: the feed works everybody out afresh at start while those caps are in force."""
    lower = settings.cloud_vm.model_copy(update={'cap_max_memory_mib': 2048})
    monkeypatch.setattr(settings, 'cloud_vm', lower)

    assert await ProducerFeed().started() > 0
    assert await ProducerFeed().reconcile() == 0


async def test_caps_a_super_admin_set_are_not_the_deployments(world, monkeypatch):
    await make_super_admin(world.acme.admin_id)
    await world.call(
        'PUT', '/portal/vm-caps', json={'max_memory_mib': 8192, 'max_vcpus': 4, 'max_idle_before_pause_s': 3600}
    )
    await ProducerFeed().drain()
    lower = settings.cloud_vm.model_copy(update={'cap_max_memory_mib': 2048})
    monkeypatch.setattr(settings, 'cloud_vm', lower)

    assert await ProducerFeed().started() == 0
