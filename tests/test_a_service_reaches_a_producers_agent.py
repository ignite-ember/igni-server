"""A service reaches a producer two ways: events for its extension, messages for its agent.

`docs/service-identity.md` §15. A message, a new chat or an action on a
chat (`POST /v1/service-items`) is posted by the producer's igni exactly
as sent; an event is handed to the extension, and one no handler took
comes back `not_handled`. Beyond a plain event, a service may send only
what its extension declares in `permissions.service` — checked here, on
every request. Files are uploaded first, kept in the organisation's own
database with the composer's limit, named by one item, fetched by the
producer's own igni, and gone when the item expires.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from tests.cloud_world import CloudOrg, platform, shipped, token_for
from tests.conftest import audit_rows

pytestmark = pytest.mark.anyio

EXTENSION = 'acme.claims'
EVERYTHING = {'service': ['messages', 'attachments', 'chats']}
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 16


async def _world(client, permissions: dict[str, object] | None = EVERYTHING) -> dict:
    acme = await CloudOrg(client).create()
    service = await shipped(client, EXTENSION, permissions=permissions)
    claims = await acme.group('Claims')
    marieke, session = await acme.person('marieke@acme.com', claims)
    await acme.switch_group(claims, EXTENSION)
    other, other_session = await acme.person('max@acme.com', claims)
    return {
        'acme': acme,
        'service': await token_for(client, service, organisation=acme.org_id),
        'marieke': marieke,
        'session': session,
        'other_session': other_session,
    }


@pytest.fixture
async def world(client):
    return await _world(client)


def _message(producer_id: str, **extra) -> dict:
    return {'producer': {'kind': 'person', 'id': producer_id}, 'kind': 'message', 'text': 'Assess CL-20431.', **extra}


async def _upload(client, world, data: bytes = PNG, filename: str = 'damage.png', media: str = 'image/png'):
    return await client.post(
        '/service-attachments',
        params={'filename': filename},
        content=data,
        headers={**world['service'], 'Content-Type': media},
    )


async def _pull(client, world) -> list[dict]:
    response = await client.post('/me/events/pull', headers=world['session'])
    assert response.status_code == 200, response.text
    return list(response.json()['events'])


# ── The items ─────────────────────────────────────────────────────


async def test_a_message_with_a_thread_reaches_the_producer_as_sent(client, world):
    accepted = await client.post(
        '/service-items',
        json=_message(world['marieke'], thread='claim:CL-20431', title='Claim CL-20431', start_run=False),
        headers=world['service'],
    )
    [item] = await _pull(client, world)

    assert accepted.status_code == 202, accepted.text
    assert {k: item[k] for k in ('kind', 'type', 'thread', 'text', 'title', 'start_run', 'attachments')} == {
        'kind': 'message',
        'type': 'message',
        'thread': 'claim:CL-20431',
        'text': 'Assess CL-20431.',
        'title': 'Claim CL-20431',
        'start_run': False,
        'attachments': [],
    }
    status = (await client.get(f'/service-events/{item["event_id"]}', headers=world['service'])).json()
    assert (status['kind'], status['thread'], status['state']) == ('message', 'claim:CL-20431', 'pending')


async def test_chats_and_actions_carry_what_they_do(client, world):
    view = {'root': 'r', 'elements': {'r': {'type': 'Text', 'props': {'text': 'Paid'}}}}
    chat = await client.post(
        '/service-items',
        json={
            'producer': {'kind': 'person', 'id': world['marieke']},
            'kind': 'chat',
            'thread': 'claim:1',
            'title': 'CL-1',
        },
        headers=world['service'],
    )
    shown = await client.post(
        '/service-items',
        json={
            'producer': {'kind': 'person', 'id': world['marieke']},
            'kind': 'action',
            'thread': 'claim:1',
            'action': {'op': 'show', 'view': view},
        },
        headers=world['service'],
    )
    first, second = await _pull(client, world)

    assert chat.status_code == shown.status_code == 202
    assert (first['kind'], first['title']) == ('chat', 'CL-1')
    assert second['action'] == {'op': 'show', 'view': view, 'title': None}


@pytest.mark.parametrize(
    'body, why',
    [
        ({'kind': 'message'}, 'a message needs text'),
        ({'kind': 'chat', 'title': 'x'}, 'a chat needs a thread'),
        ({'kind': 'chat', 'thread': 't'}, 'a chat needs a title'),
        ({'kind': 'action', 'action': {'op': 'show'}}, 'show needs a json-render view'),
        ({'kind': 'action', 'action': {'op': 'rename'}}, 'rename needs a title'),
        ({'kind': 'event', 'type': 'x', 'thread': 't'}, 'an event takes no text, thread or attachments'),
        ({'kind': 'message', 'text': 'x', 'payload': {'a': 1}}, 'a message takes no payload'),
    ],
)
async def test_an_item_that_says_the_wrong_things_is_refused(client, world, body, why):
    response = await client.post(
        '/service-items',
        json={'producer': {'kind': 'person', 'id': world['marieke']}, **body},
        headers=world['service'],
    )
    assert response.status_code == 422
    assert why in response.text


async def test_the_same_event_key_is_stored_once_for_any_kind(client, world):
    first = await client.post(
        '/service-items', json=_message(world['marieke'], event_key='k1'), headers=world['service']
    )
    again = await client.post(
        '/service-items', json=_message(world['marieke'], event_key='k1'), headers=world['service']
    )
    assert again.json() == {**first.json(), 'duplicate': True}


# ── Permissions ───────────────────────────────────────────────────


async def test_without_the_grant_a_message_a_thread_or_a_file_is_refused(client):
    world = await _world(client, permissions=None)
    message = await client.post('/service-items', json=_message(world['marieke']), headers=world['service'])
    chat = await client.post(
        '/service-items',
        json={'producer': {'kind': 'person', 'id': world['marieke']}, 'kind': 'chat', 'thread': 't', 'title': 'T'},
        headers=world['service'],
    )
    upload = await _upload(client, world)
    event = await client.post(
        '/service-events',
        json={'producer': {'kind': 'person', 'id': world['marieke']}, 'type': 'claim.created'},
        headers=world['service'],
    )

    assert (message.status_code, chat.status_code, upload.status_code) == (403, 403, 403)
    assert 'service.messages' in message.json()['detail']
    assert 'service.chats' in chat.json()['detail']
    assert 'service.attachments' in upload.json()['detail']
    assert event.status_code == 202, 'a plain event needs no grant'


async def test_a_grant_igni_does_not_know_is_refused_at_upload(client):
    with pytest.raises(AssertionError, match='permissions.service'):
        await shipped(client, 'acme.greedy', permissions={'service': ['approvals']})


# ── Attachments ───────────────────────────────────────────────────


async def test_a_file_is_named_by_an_item_and_fetched_by_its_producer_only(client, world):
    stored = (await _upload(client, world)).json()
    await client.post(
        '/service-items',
        json=_message(world['marieke'], attachments=[stored['attachment_id']]),
        headers=world['service'],
    )
    [item] = await _pull(client, world)
    path = f'/me/events/{item["event_id"]}/attachments/{stored["attachment_id"]}'
    fetched = await client.get(path, headers=world['session'])
    stranger = await client.get(path, headers=world['other_session'])

    assert stored['sha256'] == hashlib.sha256(PNG).hexdigest() and stored['size'] == len(PNG)
    assert item['attachments'] == [
        {k: stored[k] for k in ('attachment_id', 'filename', 'content_type', 'size', 'sha256')}
    ]
    assert fetched.status_code == 200 and fetched.content == PNG
    assert fetched.headers['content-type'] == 'image/png'
    assert stranger.status_code == 404


async def test_a_file_is_named_by_one_item_only_and_only_by_its_own_service(client, world):
    stored = (await _upload(client, world)).json()
    first = await client.post(
        '/service-items',
        json=_message(world['marieke'], attachments=[stored['attachment_id']]),
        headers=world['service'],
    )
    second = await client.post(
        '/service-items',
        json=_message(world['marieke'], attachments=[stored['attachment_id']]),
        headers=world['service'],
    )
    unknown = await client.post(
        '/service-items', json=_message(world['marieke'], attachments=['nope']), headers=world['service']
    )

    assert first.status_code == 202
    assert second.status_code == unknown.status_code == 400


@pytest.mark.parametrize(
    'data, filename, media, status',
    [
        (b'x' * (5 * 1024 * 1024 + 1), 'big.bin', 'application/octet-stream', 413),
        (b'', 'empty.txt', 'text/plain', 400),
        (PNG, '../etc/passwd', 'image/png', 400),
        (PNG, 'photo.png', 'not a type', 415),
    ],
)
async def test_a_file_igni_would_not_import_is_refused(client, world, data, filename, media, status):
    assert (await _upload(client, world, data, filename, media)).status_code == status


async def test_any_type_the_composer_takes_is_accepted(client, world):
    for filename, media in (
        ('form.pdf', 'application/pdf'),
        ('notes.docx', 'application/octet-stream'),
        ('a.csv', 'text/csv'),
    ):
        assert (await _upload(client, world, b'data', filename, media)).status_code == 201


async def test_a_file_expires_with_its_item(client, world, monkeypatch):
    soon = datetime.now(timezone.utc) + timedelta(minutes=5)
    stored = (await _upload(client, world)).json()
    await client.post(
        '/service-items',
        json=_message(world['marieke'], attachments=[stored['attachment_id']], expires_at=soon.isoformat()),
        headers=world['service'],
    )
    [item] = await _pull(client, world)
    later = datetime.now(timezone.utc) + timedelta(minutes=10)
    monkeypatch.setattr(
        'app.db.sql.service_attachments.datetime',
        type('Later', (), {'now': staticmethod(lambda tz=None: later)}),
    )

    gone = await client.get(
        f'/me/events/{item["event_id"]}/attachments/{stored["attachment_id"]}', headers=world['session']
    )
    assert gone.status_code == 404


# ── An event no handler took ──────────────────────────────────────


async def test_an_event_no_handler_took_reads_not_handled_and_is_recorded(client, world):
    gateway = await platform(client)
    acme = world['acme']
    sent = (
        await client.post(
            '/service-events',
            json={'producer': {'kind': 'person', 'id': world['marieke']}, 'type': 'claim.created'},
            headers=world['service'],
        )
    ).json()
    message = (await client.post('/service-items', json=_message(world['marieke']), headers=world['service'])).json()
    await client.post(f'/platform/orgs/{acme.org_id}/producers/person/{world["marieke"]}/events/pull', headers=gateway)

    acked = await client.post(
        f'/platform/orgs/{acme.org_id}/events/ack',
        json={
            'event_ids': [sent['event_id'], message['event_id']],
            'not_handled': [{'event_id': sent['event_id'], 'reason': 'no handler for claim.created'}],
        },
        headers=gateway,
    )
    event_status = (await client.get(f'/service-events/{sent["event_id"]}', headers=world['service'])).json()
    message_status = (await client.get(f'/service-events/{message["event_id"]}', headers=world['service'])).json()

    assert acked.json() == {'delivered': 2}
    assert (event_status['state'], event_status['not_handled_reason']) == (
        'not_handled',
        'no handler for claim.created',
    )
    assert message_status['state'] == 'delivered'
    assert any(row['route'].endswith('/events/ack') for row in audit_rows())


async def test_only_an_event_can_be_not_handled(client, world):
    message = (await client.post('/service-items', json=_message(world['marieke']), headers=world['service'])).json()
    await _pull(client, world)
    await client.post(
        '/me/events/ack',
        json={'event_ids': [message['event_id']], 'not_handled': [{'event_id': message['event_id'], 'reason': 'x'}]},
        headers=world['session'],
    )
    status = (await client.get(f'/service-events/{message["event_id"]}', headers=world['service'])).json()
    assert status['state'] == 'pending', "a message is the agent's: never not handled"
