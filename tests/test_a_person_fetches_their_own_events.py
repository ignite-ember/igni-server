"""igni on a person's desktop fetches their events with their session, as the gateway does for a VM.

`POST /me/events/pull` and `POST /me/events/ack` take the same shapes and
the same two-minute lease as the gateway's routes; an event is handed
out until acknowledged, expires after 14 days, and its service reads
what became of it (`docs/service-identity.md` §15; igni's `CLOUD.md` §6).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.cloud_world import CloudOrg, platform, shipped, token_for

pytestmark = pytest.mark.anyio

EXTENSION = 'igni.codeindex'


@pytest.fixture
async def world(client):
    acme = await CloudOrg(client).create()
    service = await shipped(client, EXTENSION)
    legal = await acme.group('Legal')
    ada, ada_session = await acme.person('ada@acme.com', legal)
    max_id, max_session = await acme.person('max@acme.com', legal)
    await acme.switch_group(legal, EXTENSION)
    return {
        'acme': acme,
        'service': await token_for(client, service, organisation=acme.org_id),
        'ada': ada,
        'ada_session': ada_session,
        'max': max_id,
        'max_session': max_session,
    }


async def _send(client, world, producer_id: str) -> str:
    response = await client.post(
        '/service-events',
        json={'producer': {'kind': 'person', 'id': producer_id}, 'type': 'index.ready', 'payload': {'repo': 'r1'}},
        headers=world['service'],
    )
    assert response.status_code == 202, response.text
    return str(response.json()['event_id'])


async def _pull(client, session: dict[str, str]) -> list[dict]:
    response = await client.post('/me/events/pull', headers=session)
    assert response.status_code == 200, response.text
    return list(response.json()['events'])


def _later(monkeypatch, delta: timedelta) -> None:
    monkeypatch.setattr(
        'app.db.sql.producer_events.datetime',
        type('Later', (), {'now': staticmethod(lambda tz=None: datetime.now(timezone.utc) + delta)}),
    )


async def test_a_person_pulls_and_acknowledges_their_own(client, world):
    event_id = await _send(client, world, world['ada'])

    [event] = await _pull(client, world['ada_session'])
    leased = await _pull(client, world['ada_session'])
    acked = await client.post('/me/events/ack', json={'event_ids': [event_id]}, headers=world['ada_session'])

    assert event == {
        'event_id': event_id,
        'extension_id': EXTENSION,
        'client_id': event['client_id'],
        'kind': 'event',
        'type': 'index.ready',
        'payload': {'repo': 'r1'},
        'thread': None,
        'text': None,
        'title': None,
        'start_run': True,
        'action': None,
        'attachments': [],
        'event_key': None,
        'created_at': event['created_at'],
        'expires_at': event['expires_at'],
    }
    assert leased == []
    assert acked.json() == {'delivered': 1}
    status = (await client.get(f'/service-events/{event_id}', headers=world['service'])).json()
    assert status['state'] == 'delivered'
    assert await _pull(client, world['ada_session']) == []


async def test_one_not_acknowledged_is_handed_out_again_after_the_lease(client, world, monkeypatch):
    event_id = await _send(client, world, world['ada'])
    await _pull(client, world['ada_session'])

    _later(monkeypatch, timedelta(minutes=3))

    assert [e['event_id'] for e in await _pull(client, world['ada_session'])] == [event_id]


async def test_nobody_sees_or_acknowledges_another_persons_events(client, world):
    event_id = await _send(client, world, world['ada'])

    theirs = await _pull(client, world['max_session'])
    acked = await client.post('/me/events/ack', json={'event_ids': [event_id]}, headers=world['max_session'])

    assert theirs == []
    assert acked.json() == {'delivered': 0}
    assert [e['event_id'] for e in await _pull(client, world['ada_session'])] == [event_id]


async def test_an_expired_event_is_not_handed_out(client, world, monkeypatch):
    event_id = await _send(client, world, world['ada'])

    _later(monkeypatch, timedelta(days=14, minutes=1))

    assert await _pull(client, world['ada_session']) == []
    status = (await client.get(f'/service-events/{event_id}', headers=world['service'])).json()
    assert status['state'] == 'expired'


async def test_the_gateway_and_the_desktop_share_one_lease(client, world):
    """A person in the cloud and on their desktop: whichever fetches first has it for two minutes."""
    acme = world['acme']
    gateway = await platform(client)
    await _send(client, world, world['ada'])

    [mine] = await _pull(client, world['ada_session'])
    vm = await client.post(f'/platform/orgs/{acme.org_id}/producers/person/{world["ada"]}/events/pull', headers=gateway)

    assert mine['type'] == 'index.ready'
    assert vm.json() == {'events': []}


async def test_an_app_token_is_refused(client, world):
    acme = world['acme']
    app = await acme.app()
    headers = await acme.app_token(app)

    pulled = await client.post('/me/events/pull', headers=headers)
    acked = await client.post('/me/events/ack', json={'event_ids': ['x']}, headers=headers)

    assert pulled.status_code == 403
    assert acked.status_code == 403


async def test_no_session_is_a_401(client):
    assert (await client.post('/me/events/pull')).status_code == 401
