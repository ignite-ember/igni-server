"""A person's service events go to one side: igni on their computer, or their cloud VM — never both.

igni server holds one owner per person and serves only the owner
(igni-core `docs/design/local-cloud-switch.md` §7). The group's mode
decides it where it allows one place only; in a group that runs igni in
both, the person's last switch decides (`PUT /v1/me/cloud/events-owner`),
and it is `local` until they first switch. The desktop's pull while the
cloud owns the events is a 409; the gateway's pull while the desktop owns
them gets nothing, and no VM is woken for them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from tests.cloud_world import CloudOrg, platform, shipped, token_for
from tests.conftest import audit_rows

pytestmark = pytest.mark.anyio

EXTENSION = 'igni.codeindex'


@pytest.fixture
async def world(client):
    acme = await CloudOrg(client).create()
    service = await shipped(client, EXTENSION, permissions={'service': ['messages', 'attachments']})
    legal = await acme.group('Legal')
    ada, ada_session = await acme.person('ada@acme.com', legal)
    await acme.switch_group(legal, EXTENSION)
    return {
        'acme': acme,
        'legal': legal,
        'service': await token_for(client, service, organisation=acme.org_id),
        'ada': ada,
        'ada_session': ada_session,
        'gateway': await platform(client),
    }


async def _send(client, world, **extra) -> str:
    response = await client.post(
        '/service-events',
        json={
            'producer': {'kind': 'person', 'id': world['ada']},
            'type': 'index.ready',
            'payload': {'repo': 'r1'},
            **extra,
        },
        headers=world['service'],
    )
    assert response.status_code == 202, response.text
    return str(response.json()['event_id'])


async def _mode(client, world, mode: str) -> None:
    acme = world['acme']
    response = await client.patch(f'/portal/groups/{world["legal"]}', json={'mode': mode}, headers=acme.admin)
    assert response.status_code == 200, response.text


async def _owner(client, world, owner: str):
    return await client.put('/me/cloud/events-owner', json={'owner': owner}, headers=world['ada_session'])


async def _desktop(client, world):
    return await client.post('/me/events/pull', headers=world['ada_session'])


async def _vm(client, world) -> list[dict]:
    acme = world['acme']
    response = await client.post(
        f'/platform/orgs/{acme.org_id}/producers/person/{world["ada"]}/events/pull', headers=world['gateway']
    )
    assert response.status_code == 200, response.text
    return list(response.json()['events'])


async def _woken(client, world) -> list[str]:
    response = await client.get('/platform/wakes', headers=world['gateway'])
    assert response.status_code == 200, response.text
    return [w['producer']['id'] for w in response.json()['producers'] if 'event' in w['reasons']]


async def _owner_now(client, world) -> str:
    response = await client.get('/me/cloud', headers=world['ada_session'])
    assert response.status_code == 200, response.text
    return str(response.json()['events_owner'])


def _later(monkeypatch, delta: timedelta) -> None:
    monkeypatch.setattr(
        'app.db.sql.producer_events.datetime',
        type('Later', (), {'now': staticmethod(lambda tz=None: datetime.now(timezone.utc) + delta)}),
    )


class TestTheOwnerByMode:
    async def test_both_places_start_on_the_desktop(self, client, world):
        assert await _owner_now(client, world) == 'local'

    async def test_cloud_only_is_the_cloud(self, client, world):
        await _mode(client, world, 'cloud')

        assert await _owner_now(client, world) == 'cloud'

    async def test_local_only_is_the_desktop_whatever_was_chosen(self, client, world):
        assert (await _owner(client, world, 'cloud')).status_code == 200
        await _mode(client, world, 'local')

        assert await _owner_now(client, world) == 'local'

    async def test_a_choice_comes_back_with_both_places(self, client, world):
        await _owner(client, world, 'cloud')
        await _mode(client, world, 'local')
        await _mode(client, world, 'local_and_cloud')

        assert await _owner_now(client, world) == 'cloud'


class TestChoosing:
    async def test_with_both_places_either_is_taken(self, client, world):
        to_cloud = await _owner(client, world, 'cloud')
        back = await _owner(client, world, 'local')

        assert to_cloud.status_code == 200, to_cloud.text
        assert to_cloud.json()['events_owner'] == 'cloud'
        assert back.json()['events_owner'] == 'local'
        assert back.json()['producer'] == f'person:{world["ada"]}'

    async def test_local_only_refuses_the_cloud(self, client, world):
        await _mode(client, world, 'local')

        refused = await _owner(client, world, 'cloud')
        kept = await _owner(client, world, 'local')

        assert refused.status_code == 409
        assert refused.json()['error'] == 'mode_forbids'
        assert refused.json()['detail']
        assert kept.status_code == 200

    async def test_cloud_only_refuses_the_desktop(self, client, world):
        await _mode(client, world, 'cloud')

        refused = await _owner(client, world, 'local')
        kept = await _owner(client, world, 'cloud')

        assert refused.status_code == 409
        assert refused.json()['error'] == 'mode_forbids'
        assert kept.status_code == 200

    async def test_a_refusal_changes_nothing(self, client, world):
        await _owner(client, world, 'cloud')
        await _mode(client, world, 'local')
        await _owner(client, world, 'cloud')
        await _mode(client, world, 'local_and_cloud')

        assert await _owner_now(client, world) == 'cloud'

    async def test_it_survives_a_move_to_another_group(self, client, world):
        acme = world['acme']
        await _owner(client, world, 'cloud')
        sales = await acme.group('Sales')

        moved = await client.post(f'/portal/groups/{sales}/members', json={'user_id': world['ada']}, headers=acme.admin)

        assert moved.status_code == 200, moved.text
        assert await _owner_now(client, world) == 'cloud'

    async def test_it_is_audited_with_what_it_was(self, client, world):
        await _owner(client, world, 'cloud')
        await _mode(client, world, 'local')
        await _owner(client, world, 'cloud')

        rows = [r for r in audit_rows(actor_user_id=world['ada']) if r['route'] == '/v1/me/cloud/events-owner']

        assert [(r['status_code'], r['detail']) for r in rows] == [
            (409, None),
            (200, {'events_owner': {'from': 'local', 'to': 'cloud'}}),
        ]

    async def test_an_unknown_owner_is_a_422(self, client, world):
        assert (await _owner(client, world, 'both')).status_code == 422

    async def test_an_app_cannot_choose(self, client, world):
        acme = world['acme']
        app = await acme.app()

        response = await client.put(
            '/me/cloud/events-owner', json={'owner': 'cloud'}, headers=await acme.app_token(app)
        )

        assert response.status_code == 403


class TestTheDesktopsPull:
    async def test_is_served_while_it_owns_them(self, client, world):
        event_id = await _send(client, world)

        response = await _desktop(client, world)

        assert response.status_code == 200
        assert [e['event_id'] for e in response.json()['events']] == [event_id]

    async def test_is_a_409_while_the_cloud_owns_them(self, client, world):
        await _send(client, world)
        await _owner(client, world, 'cloud')

        response = await _desktop(client, world)

        assert response.status_code == 409
        assert response.json() == {
            'error': 'events_owned_elsewhere',
            'owner': 'cloud',
            'detail': response.json()['detail'],
        }

    async def test_is_a_409_in_a_cloud_only_group(self, client, world):
        await _mode(client, world, 'cloud')

        response = await _desktop(client, world)

        assert response.status_code == 409
        assert response.json()['owner'] == 'cloud'

    async def test_finishes_what_it_leased_before_the_switch(self, client, world):
        attachment_id = await _attachment(client, world)
        item = await client.post(
            '/service-items',
            json={
                'producer': {'kind': 'person', 'id': world['ada']},
                'kind': 'message',
                'text': 'Read this.',
                'attachments': [attachment_id],
            },
            headers=world['service'],
        )
        assert item.status_code == 202, item.text
        [leased] = (await _desktop(client, world)).json()['events']
        event_id = leased['event_id']
        await _owner(client, world, 'cloud')

        attachment = await client.get(
            f'/me/events/{event_id}/attachments/{attachment_id}', headers=world['ada_session']
        )
        acked = await client.post('/me/events/ack', json={'event_ids': [event_id]}, headers=world['ada_session'])

        assert attachment.status_code == 200, attachment.text
        assert attachment.content == b'the bytes'
        assert acked.json() == {'delivered': 1}


class TestTheGatewaysPull:
    async def test_gets_nothing_while_the_desktop_owns_them(self, client, world):
        await _send(client, world)

        assert await _vm(client, world) == []
        # Still there for the desktop: the gateway took no lease.
        assert (await _desktop(client, world)).status_code == 200

    async def test_is_served_once_the_cloud_owns_them(self, client, world):
        event_id = await _send(client, world)
        await _owner(client, world, 'cloud')

        assert [e['event_id'] for e in await _vm(client, world)] == [event_id]

    async def test_an_app_is_served_as_ever(self, client, world):
        acme = world['acme']
        app = await acme.app()
        await acme.switch_app(app.client_id, EXTENSION)
        response = await client.post(
            '/service-events',
            json={'producer': {'kind': 'app', 'id': app.client_id}, 'type': 'index.ready', 'payload': {}},
            headers=world['service'],
        )
        assert response.status_code == 202, response.text

        pulled = await client.post(
            f'/platform/orgs/{acme.org_id}/producers/app/{app.client_id}/events/pull', headers=world['gateway']
        )

        assert len(pulled.json()['events']) == 1


class TestWakes:
    async def test_no_vm_is_woken_for_events_the_desktop_owns(self, client, world):
        await _send(client, world)

        assert world['ada'] not in await _woken(client, world)

    async def test_the_vm_is_woken_once_the_cloud_owns_them(self, client, world):
        await _send(client, world)
        await _owner(client, world, 'cloud')

        assert world['ada'] in await _woken(client, world)


async def test_only_the_owner_is_served(client, world, monkeypatch):
    """Replaces "the gateway and the desktop share one lease": an event lands on one side, and follows a switch."""
    first = await _send(client, world)

    vm_before = await _vm(client, world)
    [mine] = (await _desktop(client, world)).json()['events']
    await _owner(client, world, 'cloud')
    second = await _send(client, world)
    vm_now = await _vm(client, world)
    desktop_now = await _desktop(client, world)

    assert vm_before == []
    assert mine['event_id'] == first
    # The first is leased by the desktop and not acknowledged: the cloud gets the second now...
    assert [e['event_id'] for e in vm_now] == [second]
    assert desktop_now.status_code == 409
    # ...and the first after its lease, at least once and on the side that owns it now.
    _later(monkeypatch, timedelta(minutes=3))
    assert [e['event_id'] for e in await _vm(client, world)] == [first, second]


async def _attachment(client, world) -> str:
    content = b'the bytes'
    response = await client.post(
        '/service-attachments',
        params={'filename': 'notes.txt'},
        content=content,
        headers={**world['service'], 'Content-Type': 'text/plain'},
    )
    assert response.status_code in (200, 201), response.text
    assert response.json()['sha256'] == hashlib.sha256(content).hexdigest()
    return str(response.json()['attachment_id'])
