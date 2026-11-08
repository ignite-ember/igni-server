"""From the moment a person is removed, nothing they hold works — at igni server, and for those who check signatures.

igni server refuses every path on the very next request: no feed poll,
no waiting for a token to expire. The gateway and services, which check
tokens by their signature, learn of it from the revocations, which a
long poll answers within the second (`docs/service-identity.md` §21).
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet
import jwt
import pytest

from app.config import settings
from app.services.ai.schemas import ModelNotAvailableError
from app.services.cloud.vm_credential_grants import VM_CREDENTIAL, VM_SIGN_IN
from tests.cloud_world import CloudOrg, platform, shipped, token_for
from tests.conftest import auth_header, make_token

pytestmark = pytest.mark.anyio

EXTENSION = 'igni.codeindex'

#: A model pool that serves nothing: past authorisation a chat is a 404.
_NO_MODEL = AsyncMock()
_NO_MODEL.acquire = AsyncMock(side_effect=ModelNotAvailableError(model='m', available=[]))
EXCHANGE = 'urn:ietf:params:oauth:grant-type:token-exchange'
ACCESS = 'urn:ietf:params:oauth:token-type:access_token'


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    monkeypatch.setattr(settings, 'secret_encryption_key', Fernet.generate_key().decode())


@pytest.fixture
async def world(client):
    acme = await CloudOrg(client).create()
    service = await shipped(client, EXTENSION)
    legal = await acme.group('Legal')
    ada, _ = await acme.person('ada@acme.com', legal)
    await acme.switch_group(legal, EXTENSION)
    session = make_token(ada, org_id=acme.org_id)
    signed_in = await client.post('/oauth/token', data={'grant_type': VM_SIGN_IN, 'subject_token': session})
    assert signed_in.status_code == 200, signed_in.text
    return {
        'acme': acme,
        'ada': ada,
        'session': session,
        'vm_token': signed_in.json()['access_token'],
        'vm_credential': signed_in.json()['vm_credential'],
        'gateway': await platform(client),
        'service': await token_for(client, service, organisation=acme.org_id),
        'registered': service,
    }


async def _every_path(client, world) -> dict[str, int | str]:
    """Each way the person could act, as the status or OAuth error it gets."""
    session, vm = auth_header(world['session']), auth_header(world['vm_token'])
    chat = {'messages': [{'role': 'user', 'content': 'hi'}]}
    command = {'producer': {'kind': 'person', 'id': world['ada']}, 'command': 'uptime'}

    async def grant(**data: str) -> str:
        response = await client.post('/oauth/token', data=data)
        return str(response.json().get('error') or response.status_code)

    with patch('app.services.ai.pool_manager.PoolManager.get_pool', new=lambda _self: _NO_MODEL):
        chats = (
            (await client.post('/chat/completions', headers=session, json=chat)).status_code,
            (await client.post('/chat/completions', headers=vm, json=chat)).status_code,
        )
    return {
        'portal': (await client.get('/portal/vm-credentials', headers=session)).status_code,
        'extensions': (await client.get('/extensions', headers=session)).status_code,
        'chat': chats[0],
        'chat from the VM': chats[1],
        'events': (await client.post('/me/events/pull', headers=vm)).status_code,
        'mcp approvals': (await client.get('/me/mcp-approvals', headers=vm)).status_code,
        'commands': (await client.post('/vm/commands', headers=vm, json=command)).status_code,
        'service token': await grant(
            grant_type=EXCHANGE,
            subject_token=world['session'],
            subject_token_type=ACCESS,
            audience='https://codeindex.example.com',
        ),
        # Before signing in again, which would replace the credential.
        'vm credential': await grant(grant_type=VM_CREDENTIAL, vm_credential=world['vm_credential']),
        'vm sign-in': await grant(grant_type=VM_SIGN_IN, subject_token=world['session']),
    }


async def test_before_removal_every_path_works(client, world):
    paths = await _every_path(client, world)

    refused = {path: answer for path, answer in paths.items() if answer in (401, 403, 'invalid_grant')}
    assert refused == {}


async def test_the_request_after_removal_is_refused_everywhere(client, world):
    acme = world['acme']

    removed = await client.delete(f'/portal/team/{world["ada"]}', headers=acme.admin)
    paths = await _every_path(client, world)

    assert removed.status_code in (200, 204)
    allowed = {path: answer for path, answer in paths.items() if answer not in (401, 403, 'invalid_grant')}
    assert allowed == {}


async def test_the_gateway_and_services_hear_of_it_within_a_second(client, world):
    acme, ada = world['acme'], world['ada']
    start = (await client.get('/platform/revocations', headers=world['gateway'])).json()['cursor']

    async def remove_soon() -> float:
        await asyncio.sleep(0.3)
        await client.delete(f'/portal/team/{ada}', headers=acme.admin)
        return time.monotonic()

    removing = asyncio.create_task(remove_soon())
    waited = await client.get('/platform/revocations', params={'since': start, 'wait': 10}, headers=world['gateway'])
    answered = time.monotonic()
    removed_at = await removing
    seen_by_service = (await client.get('/revocations', params={'since': start}, headers=world['service'])).json()[
        'revocations'
    ]

    [entry] = waited.json()['revocations']
    assert (entry['kind'], entry['org_id'], entry['subject_id'], entry['reason']) == (
        'person',
        acme.org_id,
        ada,
        'removed',
    )
    assert entry['incarnation']
    assert entry['not_before_ms'] >= int(time.time() * 1000) - 5000
    assert answered - removed_at < 1.0
    assert [e['subject_id'] for e in seen_by_service] == [ada]


async def test_the_feed_long_poll_answers_on_the_change(client, world):
    acme, ada = world['acme'], world['ada']
    cursor = (await client.get('/platform/producers', headers=world['gateway'])).json()['cursor']

    async def deactivate_soon() -> None:
        await asyncio.sleep(0.3)
        await client.patch(f'/portal/team/{ada}/status', json={'status': 'deactivated'}, headers=acme.admin)

    task = asyncio.create_task(deactivate_soon())
    began = time.monotonic()
    rows = (
        await client.get('/platform/producers', params={'since': cursor, 'wait': 10}, headers=world['gateway'])
    ).json()['producers']
    await task

    assert time.monotonic() - began < 2.0
    assert [(r['producer']['id'], r['active']) for r in rows] == [(ada, False)]


async def test_withdrawn_sessions_are_revoked_in_every_organisation(client, world):
    start = (await client.get('/platform/revocations', headers=world['gateway'])).json()['cursor']

    await client.post('/portal/me/revoke-sessions', headers=auth_header(world['session']))
    [entry] = (await client.get('/platform/revocations', params={'since': start}, headers=world['gateway'])).json()[
        'revocations'
    ]

    assert (entry['kind'], entry['org_id'], entry['subject_id'], entry['reason']) == (
        'person',
        None,
        world['ada'],
        'sessions_withdrawn',
    )


async def test_a_deleted_app_or_space_token_is_refused_at_once(client):
    acme = await CloudOrg(client).create()
    app = await acme.app()
    app_headers = await acme.app_token(app)
    space = (await client.put(f'/apps/{app.client_id}/spaces/cust-1', headers=app_headers)).json()
    space_token = (
        await client.post(
            '/oauth/token',
            data={
                'grant_type': EXCHANGE,
                'subject_token': app_headers['Authorization'].removeprefix('Bearer '),
                'subject_token_type': ACCESS,
                'space': space['space_id'],
            },
        )
    ).json()['access_token']

    await client.delete(f'/apps/{app.client_id}/spaces/cust-1', headers=app_headers)
    space_now = await client.get('/extensions', headers=auth_header(space_token))
    await client.delete(f'/portal/apps/{app.client_id}', headers=acme.admin)
    app_now = await client.get('/extensions', headers=app_headers)

    assert (space_now.status_code, app_now.status_code) == (401, 401)


async def test_a_service_lists_what_it_serves_and_follows_them_all_on_one_cursor(client, world):
    acme, ada = world['acme'], world['ada']
    globex = await CloudOrg(client, slug='globex').create()
    start = (await client.get('/platform/revocations', headers=world['gateway'])).json()['cursor']

    served = (await client.get('/organisations/served', headers=world['service'])).json()
    await client.delete(f'/portal/team/{ada}', headers=acme.admin)
    await client.delete(f'/portal/team/{globex.admin_id}', headers=globex.admin)
    every = (
        await client.get('/revocations', params={'since': start, 'scope': 'served'}, headers=world['service'])
    ).json()['revocations']

    assert served == {'organisations': [{'org_id': acme.org_id, 'name': 'acme'}]}
    assert [(e['org_id'], e['subject_id']) for e in every] == [(acme.org_id, ada)]


async def test_revocations_are_exact_to_the_millisecond(client, world):
    acme, ada = world['acme'], world['ada']
    start = (await client.get('/platform/revocations', headers=world['gateway'])).json()['cursor']

    await client.delete(f'/portal/team/{ada}', headers=acme.admin)
    [entry] = (await client.get('/platform/revocations', params={'since': start}, headers=world['gateway'])).json()[
        'revocations'
    ]
    await asyncio.sleep(0.01)
    later = jwt.decode(make_token(ada, org_id=acme.org_id), options={'verify_signature': False})

    assert later['iat_ms'] > entry['not_before_ms']
    assert later['iat'] * 1000 <= later['iat_ms'] < later['iat'] * 1000 + 1000


async def test_a_service_starts_following_with_a_token_for_no_organisation(client, world):
    """At start-up a service knows no organisation to name: it asks for scope `revocations` instead."""
    acme, ada = world['acme'], world['ada']
    service = world['registered']
    without = await client.post('/oauth/token', data=service.authenticated('client_credentials'))
    follower = await token_for(client, service, scope='revocations')
    start = (await client.get('/platform/revocations', headers=world['gateway'])).json()['cursor']

    served = await client.get('/organisations/served', headers=follower)
    await client.delete(f'/portal/team/{ada}', headers=acme.admin)
    every = await client.get('/revocations', params={'since': start, 'scope': 'served'}, headers=follower)
    one = await client.get('/revocations', params={'since': start}, headers=follower)
    elsewhere = await client.post(
        '/service-events',
        json={'producer': {'kind': 'person', 'id': ada}, 'type': 'x', 'payload': {}},
        headers=follower,
    )

    assert without.json()['error'] == 'invalid_request'
    claims = jwt.decode(follower['Authorization'].removeprefix('Bearer '), options={'verify_signature': False})
    assert (claims['token_use'], claims['scope']) == ('service', 'revocations')
    assert 'org' not in claims
    assert [o['org_id'] for o in served.json()['organisations']] == [acme.org_id]
    assert [e['subject_id'] for e in every.json()['revocations']] == [ada]
    assert one.status_code == 400
    assert elsewhere.status_code == 401
