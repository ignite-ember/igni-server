"""Event delivery: a service asks igni to deliver an event to its extension for one producer.

Refused unless the producer has that extension on; rate-limited per
service; recorded; stored until the gateway fetches and acknowledges it
when it wakes the producer's VM — for at most 14 days, or until the
service's earlier `expires_at`. The service reads what became of each of
its events: pending, delivered, expired, or dropped with a reason
(`docs/service-identity.md` §15).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.services.cloud.producer_events import ProducerEvents
from tests.cloud_world import CloudOrg, platform, shipped, token_for
from tests.conftest import audit_rows

pytestmark = pytest.mark.anyio

EXTENSION = 'igni.codeindex'


@pytest.fixture
async def world(client):
    acme = await CloudOrg(client).create()
    service = await shipped(client, EXTENSION)
    legal = await acme.group('Legal')
    ada, _ = await acme.person('ada@acme.com', legal)
    await acme.switch_group(legal, EXTENSION)
    max_id, _ = await acme.person('max@acme.com')
    return {
        'acme': acme,
        'service': await token_for(client, service, organisation=acme.org_id),
        'ada': ada,
        'max': max_id,
    }


def _event(producer_id: str, kind: str = 'person', **extra) -> dict:
    return {'producer': {'kind': kind, 'id': producer_id}, 'type': 'index.ready', 'payload': {'repo': 'r1'}, **extra}


async def _pull(client, gateway, org_id: str, kind: str, producer_id: str) -> list[dict]:
    response = await client.post(f'/platform/orgs/{org_id}/producers/{kind}/{producer_id}/events/pull', headers=gateway)
    assert response.status_code == 200, response.text
    return list(response.json()['events'])


async def test_delivered_to_a_producer_who_has_the_extension(client, world):
    acme = world['acme']
    gateway = await platform(client)

    accepted = await client.post('/service-events', json=_event(world['ada']), headers=world['service'])
    wakes = (await client.get('/platform/wakes', headers=gateway)).json()['producers']
    [event] = await _pull(client, gateway, acme.org_id, 'person', world['ada'])
    again = await _pull(client, gateway, acme.org_id, 'person', world['ada'])
    acked = await client.post(
        f'/platform/orgs/{acme.org_id}/events/ack', json={'event_ids': [event['event_id']]}, headers=gateway
    )

    assert accepted.status_code == 202, accepted.text
    expires = datetime.fromisoformat(accepted.json()['expires_at'])
    assert timedelta(days=13, hours=23) < expires - datetime.now(timezone.utc) <= timedelta(days=14)
    assert wakes == [{'org_id': acme.org_id, 'producer': {'kind': 'person', 'id': world['ada']}, 'reasons': ['event']}]
    assert (event['extension_id'], event['type'], event['payload']) == (EXTENSION, 'index.ready', {'repo': 'r1'})
    assert again == []
    assert acked.json() == {'delivered': 1}
    status = (await client.get(f'/service-events/{event["event_id"]}', headers=world['service'])).json()
    assert (status['state'], status['dropped_reason']) == ('delivered', None)
    assert status['delivered_at']
    assert (await client.get('/platform/wakes', headers=gateway)).json()['producers'] == []


async def test_refused_for_a_producer_without_the_extension(client, world):
    response = await client.post('/service-events', json=_event(world['max']), headers=world['service'])

    assert response.status_code == 403


async def test_an_app_gets_one_when_its_own_list_has_the_extension(client, world):
    acme = world['acme']
    app = await acme.app()

    refused = await client.post('/service-events', json=_event(app.client_id, 'app'), headers=world['service'])
    await acme.switch_app(app.client_id, EXTENSION)
    accepted = await client.post('/service-events', json=_event(app.client_id, 'app'), headers=world['service'])

    assert refused.status_code == 403
    assert accepted.status_code == 202


async def test_a_producer_of_another_organisation_is_not_found(client, world):
    globex = await CloudOrg(client, slug='globex').create()
    bob, _ = await globex.person('bob@globex.com')

    response = await client.post('/service-events', json=_event(bob), headers=world['service'])

    assert response.status_code == 404


async def test_the_same_event_key_is_stored_once(client, world):
    first = await client.post('/service-events', json=_event(world['ada'], event_key='k1'), headers=world['service'])
    second = await client.post('/service-events', json=_event(world['ada'], event_key='k1'), headers=world['service'])

    assert second.json()['event_id'] == first.json()['event_id']
    assert second.json()['duplicate'] is True


async def test_switched_off_before_delivery_it_is_dropped_and_says_why(client, world):
    acme = world['acme']
    gateway = await platform(client)
    sent = (await client.post('/service-events', json=_event(world['ada']), headers=world['service'])).json()
    groups = (await client.get('/portal/groups', headers=acme.admin)).json()['groups']
    legal = next(g['id'] for g in groups if g['name'] == 'Legal')

    await acme.switch_group(legal, EXTENSION, enabled=False)

    assert await _pull(client, gateway, acme.org_id, 'person', world['ada']) == []
    status = (await client.get(f'/service-events/{sent["event_id"]}', headers=world['service'])).json()
    assert (status['state'], status['dropped_reason']) == ('dropped', 'extension_switched_off')


def _later(monkeypatch, delta: timedelta) -> None:
    """The clock the events' store reads, moved ahead."""
    monkeypatch.setattr(
        'app.db.sql.producer_events.datetime',
        type('Later', (), {'now': staticmethod(lambda tz=None: datetime.now(timezone.utc) + delta)}),
    )


async def test_an_expired_event_is_not_handed_out_and_its_service_reads_expired(client, world, monkeypatch):
    acme = world['acme']
    gateway = await platform(client)
    soon = datetime.now(timezone.utc) + timedelta(minutes=5)
    sent = (
        await client.post(
            '/service-events', json=_event(world['ada'], expires_at=soon.isoformat()), headers=world['service']
        )
    ).json()
    past = await client.post(
        '/service-events',
        json=_event(world['ada'], expires_at=(datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()),
        headers=world['service'],
    )

    _later(monkeypatch, timedelta(minutes=10))

    assert past.status_code == 400
    assert await _pull(client, gateway, acme.org_id, 'person', world['ada']) == []
    assert (await client.get('/platform/wakes', headers=gateway)).json()['producers'] == []
    status = (await client.get(f'/service-events/{sent["event_id"]}', headers=world['service'])).json()
    assert status['state'] == 'expired'


async def test_an_event_waits_at_most_fourteen_days(client, world):
    week = datetime.now(timezone.utc) + timedelta(days=7)
    month = datetime.now(timezone.utc) + timedelta(days=30)

    kept = await client.post(
        '/service-events', json=_event(world['ada'], expires_at=week.isoformat()), headers=world['service']
    )
    cut = await client.post(
        '/service-events', json=_event(world['ada'], expires_at=month.isoformat()), headers=world['service']
    )

    assert datetime.fromisoformat(kept.json()['expires_at']) == week
    assert datetime.fromisoformat(cut.json()['expires_at']) <= datetime.now(timezone.utc) + timedelta(days=14)


async def test_the_maximum_is_a_setting(client, world, monkeypatch):
    monkeypatch.setattr(settings, 'service_event_max_wait_seconds', 3600)

    bounded = await client.post('/service-events', json=_event(world['ada']), headers=world['service'])

    expires = datetime.fromisoformat(bounded.json()['expires_at'])
    assert expires <= datetime.now(timezone.utc) + timedelta(seconds=3601)


async def test_a_service_lists_its_own_events_by_state_and_since(client, world):
    acme = world['acme']
    gateway = await platform(client)
    first = (await client.post('/service-events', json=_event(world['ada']), headers=world['service'])).json()
    second = (await client.post('/service-events', json=_event(world['ada']), headers=world['service'])).json()
    other = await shipped(client, 'igni.artifacts')
    other_headers = await token_for(client, other, organisation=acme.org_id)

    every = (await client.get('/service-events', headers=world['service'])).json()['events']
    await client.post(
        f'/platform/orgs/{acme.org_id}/events/ack', json={'event_ids': [first['event_id']]}, headers=gateway
    )
    pending = (await client.get('/service-events?state=pending', headers=world['service'])).json()['events']
    changed = (
        await client.get('/service-events', params={'since': every[-1]['updated_at']}, headers=world['service'])
    ).json()['events']

    assert [e['event_id'] for e in every] == [first['event_id'], second['event_id']]
    assert [e['event_id'] for e in pending] == [second['event_id']]
    assert [(e['event_id'], e['state']) for e in changed] == [(first['event_id'], 'delivered')]
    assert (await client.get('/service-events', headers=other_headers)).json()['events'] == []
    assert (await client.get(f'/service-events/{first["event_id"]}', headers=other_headers)).status_code == 404


async def test_the_retention_round_expires_what_is_due_and_deletes_what_closed_long_ago(client, world, monkeypatch):
    sent = (await client.post('/service-events', json=_event(world['ada']), headers=world['service'])).json()

    _later(monkeypatch, timedelta(days=15))
    await ProducerEvents().close_out()
    expired = (await client.get(f'/service-events/{sent["event_id"]}', headers=world['service'])).json()
    _later(monkeypatch, timedelta(days=15 + 31))
    purged = await ProducerEvents().close_out()

    assert expired['state'] == 'expired'
    assert purged == 1
    assert (await client.get(f'/service-events/{sent["event_id"]}', headers=world['service'])).status_code == 404


async def test_a_service_is_never_told_to_slow_down(client, world):
    """There is no per-service event limit: a burst is accepted whole, never answered 429."""
    codes = [
        (await client.post('/service-events', json=_event(world['ada']), headers=world['service'])).status_code
        for _ in range(25)
    ]

    assert codes == [202] * 25


async def test_only_a_service_asks_and_only_a_platform_client_fetches(client, world):
    acme = world['acme']
    _, ada_headers = await acme.person('zed@acme.com')

    assert (await client.post('/service-events', json=_event(world['ada']), headers=ada_headers)).status_code == 401
    pull = await client.post(
        f'/platform/orgs/{acme.org_id}/producers/person/{world["ada"]}/events/pull', headers=world['service']
    )
    assert pull.status_code == 401


async def test_the_gateway_gives_up_and_the_service_reads_why(client, world):
    acme = world['acme']
    gateway = await platform(client)
    sent = (await client.post('/service-events', json=_event(world['ada']), headers=world['service'])).json()
    await _pull(client, gateway, acme.org_id, 'person', world['ada'])

    failed = await client.post(
        f'/platform/orgs/{acme.org_id}/events/fail',
        json={'event_ids': [sent['event_id']], 'reason': 'igni did not take it after 5 tries'},
        headers=gateway,
    )
    again = await client.post(
        f'/platform/orgs/{acme.org_id}/events/fail',
        json={'event_ids': [sent['event_id']], 'reason': 'x'},
        headers=gateway,
    )

    assert failed.json() == {'failed': 1}
    assert again.json() == {'failed': 0}
    status = (await client.get(f'/service-events/{sent["event_id"]}', headers=world['service'])).json()
    assert (status['state'], status['failed_reason']) == ('failed', 'igni did not take it after 5 tries')
    assert (await client.get('/platform/wakes', headers=gateway)).json()['producers'] == []
    assert await _pull(client, gateway, acme.org_id, 'person', world['ada']) == []
    assert any(row['route'].endswith('/events/fail') for row in audit_rows())
