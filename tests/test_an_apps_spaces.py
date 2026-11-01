"""Spaces: an app declares one per end user, each a producer with a VM of its own.

The app declares, reads, lists and deletes them with its own token, and
acts as one by exchanging that token for the space's. A space inherits
its app's profile and extensions, holds a VM credential like an app's,
can be sent events, is limited by the platform's caps and its
organisation's quota, and is in the gateway's feed with kind `space`
(`docs/service-identity.md` §17).
"""

from __future__ import annotations

from cryptography.fernet import Fernet
import jwt
import pytest

from app.config import settings
from app.services.cloud.vm_credentials import VM_CREDENTIAL
from tests.cloud_world import CloudOrg, make_super_admin, platform, shipped, token_for
from tests.conftest import auth_header

pytestmark = pytest.mark.anyio

EXTENSION = 'igni.codeindex'
EXCHANGE = 'urn:ietf:params:oauth:grant-type:token-exchange'
ACCESS = 'urn:ietf:params:oauth:token-type:access_token'


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    """A new space's credential is kept encrypted until it is picked up."""
    monkeypatch.setattr(settings, 'secret_encryption_key', Fernet.generate_key().decode())


class Shop:
    """An organisation with an app that serves its own customers."""

    def __init__(self, client, acme: CloudOrg) -> None:
        self.client = client
        self.acme = acme
        self.app_id = ''
        self.app: dict[str, str] = {}
        self.gateway: dict[str, str] = {}

    async def open(self) -> Shop:
        app = await self.acme.app('Tutor')
        self.app_id = app.client_id
        self.app = await self.acme.app_token(app)
        self.gateway = await platform(self.client)
        return self

    def path(self, external_id: str = '', app_id: str | None = None) -> str:
        base = f'/apps/{app_id or self.app_id}/spaces'
        return f'{base}/{external_id}' if external_id else base

    async def declare(self, external_id: str) -> dict:
        response = await self.client.put(self.path(external_id), headers=self.app)
        assert response.status_code in (200, 201), response.text
        return dict(response.json())

    async def as_space(self, space_id: str, headers: dict[str, str] | None = None):
        token = (headers or self.app)['Authorization'].removeprefix('Bearer ')
        return await self.client.post(
            '/oauth/token',
            data={'grant_type': EXCHANGE, 'subject_token': token, 'subject_token_type': ACCESS, 'space': space_id},
        )

    async def feed(self) -> dict[str, dict]:
        response = await self.client.get('/platform/producers', headers=self.gateway)
        assert response.status_code == 200, response.text
        return {row['producer']['id']: row for row in response.json()['producers']}


@pytest.fixture
async def shop(client) -> Shop:
    return await Shop(client, await CloudOrg(client).create()).open()


class TestDeclaring:
    async def test_declaring_is_idempotent(self, client, shop):
        first = await client.put(shop.path('cust-1'), headers=shop.app)
        again = await client.put(shop.path('cust-1'), headers=shop.app)
        read = await client.get(shop.path('cust-1'), headers=shop.app)

        assert (first.status_code, again.status_code) == (201, 200)
        space = first.json()
        assert again.json() == space == read.json()
        assert space == {
            'space_id': space['space_id'],
            'app_id': shop.app_id,
            'org_id': shop.acme.org_id,
            'external_id': 'cust-1',
            'producer': {'kind': 'space', 'id': space['space_id']},
            'created_at': space['created_at'],
        }

    async def test_listed_in_pages_by_external_id(self, client, shop):
        for name in ('c', 'a', 'b'):
            await shop.declare(name)

        first = (await client.get(shop.path(), params={'limit': 2}, headers=shop.app)).json()
        rest = (await client.get(shop.path(), params={'limit': 2, 'after': first['next']}, headers=shop.app)).json()

        assert [s['external_id'] for s in first['spaces']] == ['a', 'b']
        assert first['next'] == 'b'
        assert [s['external_id'] for s in rest['spaces']] == ['c']
        assert rest['next'] is None

    async def test_only_the_app_itself_manages_its_spaces(self, client, shop):
        other = await shop.acme.app('Other')
        other_headers = await shop.acme.app_token(other)
        _, person = await shop.acme.person('ada@acme.com')

        assert (await client.put(shop.path('x'), headers=other_headers)).status_code == 403
        assert (await client.put(shop.path('x'), headers=person)).status_code == 403
        assert (await client.put(shop.path('x'), headers=shop.gateway)).status_code == 401
        assert (await client.get(shop.path('nobody'), headers=shop.app)).status_code == 404


class TestActingAsASpace:
    async def test_the_app_trades_its_token_for_its_spaces(self, client, shop):
        space = await shop.declare('cust-1')

        exchanged = await shop.as_space(space['space_id'])

        assert exchanged.status_code == 200, exchanged.text
        claims = jwt.decode(exchanged.json()['access_token'], options={'verify_signature': False})
        assert (claims['token_use'], claims['kind'], claims['sub']) == ('space', 'space', space['space_id'])
        assert (claims['app'], claims['org'], claims['external_id']) == (shop.app_id, shop.acme.org_id, 'cust-1')
        assert 'group' not in claims

    async def test_not_another_apps_space_nor_with_another_token(self, client, shop):
        space = await shop.declare('cust-1')
        other = await shop.acme.app('Other')
        _, person = await shop.acme.person('ada@acme.com')

        theirs = await shop.as_space(space['space_id'], await shop.acme.app_token(other))
        personal = await shop.as_space(space['space_id'], person)

        assert theirs.json()['error'] == 'invalid_grant'
        assert personal.json()['error'] == 'invalid_grant'

    async def test_a_space_has_its_apps_extensions_and_profile(self, client, shop):
        await shipped(client, EXTENSION)
        await shop.acme.switch_app(shop.app_id, EXTENSION)
        space = await shop.declare('cust-1')
        token = auth_header((await shop.as_space(space['space_id'])).json()['access_token'])

        listed = (await client.get('/extensions', headers=token)).json()
        rows = await shop.feed()
        its, apps = rows[space['space_id']], rows[shop.app_id]

        assert listed['producer'] == {'kind': 'space', 'id': space['space_id']}
        assert [e['extension_id'] for e in listed['extensions']] == [EXTENSION]
        assert (its['profile_version'], its['profile']) == (apps['profile_version'], apps['profile'])

    async def test_only_people_use_the_person_routes(self, client, shop):
        space = await shop.declare('cust-1')
        token = auth_header((await shop.as_space(space['space_id'])).json()['access_token'])

        assert (await client.post('/me/events/pull', headers=token)).status_code == 403
        assert (await client.get('/me/mcp-approvals', headers=token)).status_code == 403

    async def test_a_space_runs_commands_in_its_own_vm(self, client, shop):
        space = await shop.declare('cust-1')
        token = auth_header((await shop.as_space(space['space_id'])).json()['access_token'])

        response = await client.post(
            '/vm/commands',
            json={'producer': {'kind': 'space', 'id': space['space_id']}, 'command': 'uptime'},
            headers=token,
        )

        assert response.status_code == 202, response.text


class TestItsVm:
    async def test_its_credential_is_picked_up_once_and_buys_a_space_token(self, client, shop):
        space = await shop.declare('cust-1')
        pickup = f'/platform/orgs/{shop.acme.org_id}/producers/space/{space["space_id"]}/vm-credential/pickup'

        first = await client.post(pickup, headers=shop.gateway)
        second = await client.post(pickup, headers=shop.gateway)
        exchanged = await client.post(
            '/oauth/token', data={'grant_type': VM_CREDENTIAL, 'vm_credential': first.json()['vm_credential']}
        )

        assert first.status_code == 200, first.text
        assert second.status_code == 404
        claims = jwt.decode(exchanged.json()['access_token'], options={'verify_signature': False})
        assert (claims['token_use'], claims['sub']) == ('space', space['space_id'])

    async def test_the_feed_lists_it_with_its_owner_and_its_apps_profile(self, client, shop):
        space = await shop.declare('cust-1')

        rows = await shop.feed()
        row, apps = rows[space['space_id']], rows[shop.app_id]

        assert row['producer'] == {'kind': 'space', 'id': space['space_id']}
        assert row['owner'] == {'kind': 'app', 'id': shop.app_id}
        assert (row['active'], row['removed']) == (True, False)
        assert (row['profile_version'], row['packages_version']) == (apps['profile_version'], apps['packages_version'])
        assert row['profile'] == apps['profile'] is not None

    async def test_events_reach_a_space_whose_app_has_the_extension(self, client, shop):
        service = await shipped(client, EXTENSION)
        sender = await token_for(client, service, organisation=shop.acme.org_id)
        space = await shop.declare('cust-1')
        event = {'producer': {'kind': 'space', 'id': space['space_id']}, 'type': 'index.ready', 'payload': {}}

        refused = await client.post('/service-events', json=event, headers=sender)
        await shop.acme.switch_app(shop.app_id, EXTENSION)
        accepted = await client.post('/service-events', json=event, headers=sender)
        wakes = (await client.get('/platform/wakes', headers=shop.gateway)).json()['producers']
        pulled = await client.post(
            f'/platform/orgs/{shop.acme.org_id}/producers/space/{space["space_id"]}/events/pull',
            headers=shop.gateway,
        )

        assert refused.status_code == 403
        assert accepted.status_code == 202, accepted.text
        assert {'kind': 'space', 'id': space['space_id']} in [w['producer'] for w in wakes]
        assert [e['event_id'] for e in pulled.json()['events']] == [accepted.json()['event_id']]


class TestDeleting:
    async def test_a_deleted_space_is_gone_everywhere(self, client, shop):
        service = await shipped(client, EXTENSION)
        sender = await token_for(client, service, organisation=shop.acme.org_id)
        await shop.acme.switch_app(shop.app_id, EXTENSION)
        space = await shop.declare('cust-1')
        sent = await client.post(
            '/service-events',
            json={'producer': {'kind': 'space', 'id': space['space_id']}, 'type': 'index.ready', 'payload': {}},
            headers=sender,
        )
        await shop.feed()

        deleted = await client.delete(shop.path('cust-1'), headers=shop.app)
        feed = await shop.feed()
        event = (await client.get(f'/service-events/{sent.json()["event_id"]}', headers=sender)).json()
        pickup = await client.post(
            f'/platform/orgs/{shop.acme.org_id}/producers/space/{space["space_id"]}/vm-credential/pickup',
            headers=shop.gateway,
        )
        again = await shop.declare('cust-1')

        assert deleted.status_code == 204
        assert (feed[space['space_id']]['active'], feed[space['space_id']]['removed']) == (False, True)
        assert feed[space['space_id']]['owner'] == {'kind': 'app', 'id': shop.app_id}
        assert (event['state'], event['dropped_reason']) == ('dropped', 'producer_removed')
        assert pickup.status_code == 404
        assert (await shop.as_space(space['space_id'])).json()['error'] == 'invalid_grant'
        assert again['space_id'] != space['space_id']

    async def test_deleting_the_app_deletes_its_spaces(self, client, shop):
        spaces = [await shop.declare(name) for name in ('a', 'b')]
        await shop.feed()

        await client.delete(f'/portal/apps/{shop.app_id}', headers=shop.acme.admin)
        feed = await shop.feed()

        assert [(feed[s['space_id']]['active'], feed[s['space_id']]['removed']) for s in spaces] == [(False, True)] * 2


class TestLimits:
    async def test_the_caps_and_the_organisations_own_limits(self, client, shop):
        acme = shop.acme
        quota = f'/portal/orgs/{acme.org_id}/space-quota'
        await make_super_admin(acme.admin_id)

        caps = await client.put('/portal/space-caps', json={'max_spaces_per_app': 3}, headers=acme.admin)
        above = await client.put(quota, json={'max_spaces_per_app': 4}, headers=acme.admin)
        own = await client.put(quota, json={'max_spaces_per_app': 2}, headers=acme.admin)
        made = [await client.put(shop.path(name), headers=shop.app) for name in ('a', 'b', 'c')]
        read = (await client.get(quota, headers=acme.admin)).json()

        assert caps.json()['max_spaces_per_app'] == 3
        assert above.status_code == 400
        assert own.status_code == 200, own.text
        assert [r.status_code for r in made] == [201, 201, 409]
        assert 'at most 2 spaces' in made[2].json()['detail']
        assert (read['spaces'], read['max_spaces_per_app'], read['caps']['max_spaces_per_app']) == (2, 2, 3)

    async def test_only_the_super_admin_sets_the_caps(self, client, shop):
        refused = await client.put('/portal/space-caps', json={'max_spaces_per_org': 1}, headers=shop.acme.admin)

        assert refused.status_code == 403
        assert (await client.get('/portal/space-caps', headers=shop.acme.admin)).json()['max_spaces_per_org'] is None


SERVICE_URL = 'https://codeindex.example.com'


async def _for_service(client, headers: dict[str, str], **extra: str):
    return await client.post(
        '/oauth/token',
        data={
            'grant_type': EXCHANGE,
            'subject_token': headers['Authorization'].removeprefix('Bearer '),
            'subject_token_type': ACCESS,
            'audience': SERVICE_URL,
            **extra,
        },
    )


class TestATokenForAService:
    """§9: an app or a space trades its own token for one for a service, as a person's session is."""

    async def test_an_app_gets_an_app_token_for_the_service(self, client, shop):
        response = await _for_service(client, shop.app)

        assert response.status_code == 200, response.text
        assert response.json()['issued_token_type'] == ACCESS
        claims = jwt.decode(response.json()['access_token'], options={'verify_signature': False})
        assert (claims['token_use'], claims['aud'], claims['sub']) == ('app', SERVICE_URL, shop.app_id)
        assert (claims['kind'], claims['org'], claims['name']) == ('app', shop.acme.org_id, 'Tutor')

    async def test_a_space_gets_a_space_token_for_the_service(self, client, shop):
        space = await shop.declare('cust-1')
        token = auth_header((await shop.as_space(space['space_id'])).json()['access_token'])

        response = await _for_service(client, token)

        claims = jwt.decode(response.json()['access_token'], options={'verify_signature': False})
        assert (claims['token_use'], claims['aud'], claims['sub']) == ('space', SERVICE_URL, space['space_id'])
        assert (claims['kind'], claims['app'], claims['external_id']) == ('space', shop.app_id, 'cust-1')
        assert claims['org'] == shop.acme.org_id

    async def test_another_organisation_or_a_deleted_space_is_refused(self, client, shop):
        space = await shop.declare('cust-1')
        token = auth_header((await shop.as_space(space['space_id'])).json()['access_token'])
        globex = await CloudOrg(client, slug='globex').create()

        elsewhere = await _for_service(client, shop.app, organisation=globex.org_id)
        await client.delete(shop.path('cust-1'), headers=shop.app)
        gone = await _for_service(client, token)
        garbage = await _for_service(client, {'Authorization': 'Bearer nonsense'})

        assert elsewhere.json()['error'] == 'invalid_target'
        assert gone.json()['error'] == 'invalid_grant'
        assert garbage.json()['error'] == 'invalid_grant'
