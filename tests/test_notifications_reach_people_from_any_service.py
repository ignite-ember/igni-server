"""Notifications: an extension's service says what happened, igni tells people.

`docs/service-identity.md` §10.1. The service posts to
`POST /v1/service-notifications` with its service token; igni server
decides the rest, so no service has to:

* **who** — each recipient, by user id or address, resolved to an active
  member of the token's organisation; everybody else dropped, and the
  actor never told about their own act;
* **once** — one row per person per `event_key`, however often it retries;
* **from whom** — the source is the extension the service uploaded, never
  the request, and a `target.page` must be one of its pages.

Then the igni host on the person's machine lists (which marks delivered),
counts per extension, marks read, and follows a stream that only says
"list again" — across every organisation the person is an active member
of, each of which keeps its notifications in its own database.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.services.notifications import NotificationStream
from app.services.notifications.listeners import Listeners
from tests.conftest import add_member, auth_header, create_org, create_user, make_token
from tests.registered_service import ExtensionBundle, RegisteredService

pytestmark = pytest.mark.asyncio

ARTIFACTS = 'igni.artifacts'
CODEINDEX = 'igni.codeindex'


def _manifest(extension_id: str, *pages: str) -> dict[str, object]:
    return {
        'id': extension_id,
        'version': '1.0.0',
        'name': extension_id,
        'contributes': {'pages': [{'kind': page, 'label': page} for page in pages]},
    }


async def _uploaded(client, *, extension_id: str | None, pages: tuple[str, ...] = ()) -> RegisteredService:
    """An approved service that has uploaded its extension — or not, when
    `extension_id` is None."""
    service = RegisteredService(name=extension_id or 'No extension', base_url=f'https://{uuid.uuid4().hex[:6]}.test')
    await service.approve()
    if extension_id is not None:
        bundle = ExtensionBundle.of(_manifest(extension_id, *pages))
        uploaded = await client.put(
            f'/clients/{service.client_id}/extension',
            content=bundle.archive,
            headers={
                'Content-Type': 'application/gzip',
                'X-Igni-Bundle-Signature': bundle.signed_by(service, extension=extension_id, version='1.0.0'),
            },
        )
        assert uploaded.status_code == 200, uploaded.text
    return service


async def _token_for(client, service: RegisteredService, org_id: str) -> dict[str, str]:
    """The service's token for one organisation."""
    token = await client.post('/oauth/token', data=service.authenticated('client_credentials', organisation=org_id))
    assert token.status_code == 200, token.text
    return auth_header(token.json()['access_token'])


async def _service(client, org_id: str, *, extension_id: str | None, pages: tuple[str, ...] = ()) -> dict[str, str]:
    """An approved service's token for `org_id`, having uploaded its
    extension — or not, when `extension_id` is None."""
    return await _token_for(client, await _uploaded(client, extension_id=extension_id, pages=pages), org_id)


async def _person(db, org_id: str | None, *, status: str = 'active', domain: str = 'acme.test') -> dict[str, str]:
    uid = f'u-{uuid.uuid4().hex[:8]}'
    email = f'{uid}@{domain}'
    await create_user(db, user_id=uid, email=email)
    if org_id is not None:
        await add_member(db, org_id=org_id, user_id=uid, status=status)
    return {'user_id': uid, 'email': email, 'headers': auth_header(make_token(uid))}


def _note(*recipients: str, **fields: object) -> dict[str, object]:
    return {
        'kind': 'comment',
        'title': 'Runbook',
        'body': 'Ada: can we drop step 3?',
        'target': {'page': ARTIFACTS, 'params': {'slug': 'runbook', 'comment': 'c1'}},
        'event_key': f'comment:{uuid.uuid4().hex[:6]}',
        'recipients': list(recipients),
        **fields,
    }


@pytest.fixture
async def org(db) -> str:
    owner = f'owner-{uuid.uuid4().hex[:8]}'
    await create_user(db, user_id=owner, email=f'{owner}@acme.test')
    created = await create_org(db, owner_user_id=owner, slug=f'n-{uuid.uuid4().hex[:6]}')
    return str(created.id)


@pytest.fixture
async def ada(db, org) -> dict[str, str]:
    return await _person(db, org)


@pytest.fixture
async def max_(db, org) -> dict[str, str]:
    return await _person(db, org)


@pytest.fixture
async def artifacts(client, org) -> dict[str, str]:
    return await _service(client, org, extension_id=ARTIFACTS, pages=(ARTIFACTS, 'igni.artifacts.requests'))


@pytest.fixture
def notify(client, artifacts):
    async def _notify(*notes: dict[str, object], headers: dict[str, str] | None = None):
        return await client.post(
            '/service-notifications', json={'notifications': list(notes)}, headers=headers or artifacts
        )

    return _notify


async def _listed(client, person: dict[str, str], **params: object) -> list[dict]:
    response = await client.get('/notifications', params=params, headers=person['headers'])
    assert response.status_code == 200, response.text
    return response.json()['notifications']


class TestWhoIsTold:
    async def test_recipients_are_resolved_by_id_and_by_address(self, notify, client, ada, max_):
        response = await notify(_note(ada['user_id'], max_['email'].upper()))

        assert response.status_code == 200, response.text
        assert response.json() == {'delivered': 2}
        assert len(await _listed(client, ada)) == 1
        assert len(await _listed(client, max_)) == 1

    async def test_one_person_named_twice_is_told_once(self, notify, client, ada):
        response = await notify(_note(ada['user_id'], ada['email']))

        assert response.json() == {'delivered': 1}

    async def test_somebody_in_no_organisation_is_dropped(self, notify, db):
        loner = await _person(db, None)

        response = await notify(_note(loner['user_id'], loner['email']))

        assert response.status_code == 200, response.text
        assert response.json() == {'delivered': 0}

    async def test_a_member_of_another_organisation_is_dropped(self, notify, db, client):
        """The token is for one organisation, and so is the row."""
        outsider = await _person(db, None, domain='globex.test')
        await create_org(db, owner_user_id=outsider['user_id'], slug=f'g-{uuid.uuid4().hex[:6]}')

        response = await notify(_note(outsider['user_id'], outsider['email']))

        assert response.json() == {'delivered': 0}
        assert await _listed(client, outsider) == []

    async def test_somebody_who_left_is_dropped(self, notify, db, org):
        former = await _person(db, org, status='deactivated')

        response = await notify(_note(former['user_id'], former['email']))

        assert response.json() == {'delivered': 0}

    async def test_an_unknown_identity_is_dropped_not_an_error(self, notify, ada):
        response = await notify(_note('nobody@acme.test', 'u-does-not-exist', ada['user_id']))

        assert response.json() == {'delivered': 1}

    async def test_the_actor_is_never_told_about_their_own_act(self, notify, client, ada, max_):
        by_id = await notify(_note(ada['user_id'], max_['user_id'], actor=ada['user_id']))
        by_address = await notify(_note(ada['email'], max_['user_id'], actor=ada['email']))

        assert by_id.json() == {'delivered': 1}
        assert by_address.json() == {'delivered': 1}
        assert await _listed(client, ada) == []

    async def test_the_actor_is_shown_by_address(self, notify, client, ada, max_):
        await notify(_note(max_['user_id'], actor=ada['user_id']))

        [row] = await _listed(client, max_)
        assert row['actor'] == ada['email']

    async def test_no_recipients_is_not_an_error(self, notify):
        response = await notify(_note())

        assert response.json() == {'delivered': 0}


class TestOnce:
    async def test_a_retried_event_is_delivered_once(self, notify, client, ada):
        note = _note(ada['user_id'], event_key='comment:c1')

        first = await notify(note)
        again = await notify(note)

        assert first.json() == {'delivered': 1}
        assert again.json() == {'delivered': 0}, 'the same event was delivered twice'
        assert len(await _listed(client, ada)) == 1

    async def test_the_key_is_per_person(self, notify, ada, max_):
        """One comment concerns several people; a key one of them has seen
        does not stop the other being told."""
        await notify(_note(ada['user_id'], event_key='comment:c2'))

        response = await notify(_note(ada['user_id'], max_['user_id'], event_key='comment:c2'))

        assert response.json() == {'delivered': 1}

    async def test_an_event_without_a_key_can_recur(self, notify, ada):
        first = await notify(_note(ada['user_id'], event_key=''))
        second = await notify(_note(ada['user_id'], event_key=''))

        assert (first.json(), second.json()) == ({'delivered': 1}, {'delivered': 1})

    async def test_in_a_batch_the_first_verb_for_a_person_wins(self, notify, client, db, org, ada, max_):
        """One comment, three verbs, one key. Ada owns the document and is
        also mentioned, so she gets the mention — which comes first — and
        nothing else; Max is in the thread and gets the reply. Resolution
        comes before the check, so naming Ada by address in one verb and by
        id in another changes nothing."""
        thread = await _person(db, org)
        key = 'comment:c7'

        response = await notify(
            _note(ada['email'], kind='mention', event_key=key),
            _note(ada['user_id'], kind='comment', event_key=key),
            _note(ada['user_id'], max_['email'], thread['user_id'], kind='reply', event_key=key),
        )

        assert response.json() == {'delivered': 3}
        assert [row['kind'] for row in await _listed(client, ada)] == ['mention']
        assert [row['kind'] for row in await _listed(client, max_)] == ['reply']
        assert [row['kind'] for row in await _listed(client, thread)] == ['reply']

    async def test_a_shared_key_reaches_every_grantee_once(self, notify, client, db, org, ada, max_):
        """`shared:<doc>` is the same key for everybody it is shared with:
        the check is per person, not global."""
        third = await _person(db, org)
        key = 'shared:runbook'

        first = await notify(_note(ada['user_id'], max_['email'], kind='shared_with_you', event_key=key))
        later = await notify(
            _note(ada['user_id'], max_['user_id'], third['email'], kind='shared_with_you', event_key=key)
        )

        assert (first.json(), later.json()) == ({'delivered': 2}, {'delivered': 1})
        for person in (ada, max_, third):
            assert len(await _listed(client, person)) == 1

    async def test_one_extensions_key_does_not_swallow_anothers(self, notify, client, org, ada):
        codeindex = await _service(client, org, extension_id=CODEINDEX, pages=(CODEINDEX,))
        await notify(_note(ada['user_id'], event_key='done:1'))

        response = await notify(
            _note(ada['user_id'], event_key='done:1', target={'page': CODEINDEX}), headers=codeindex
        )

        assert response.json() == {'delivered': 1}


class TestFromWhom:
    async def test_the_source_is_the_uploaded_extension_not_the_request(self, notify, client, ada):
        await notify(_note(ada['user_id'], source='igni.somebody-else'))

        [row] = await _listed(client, ada)
        assert row['source'] == ARTIFACTS
        assert row['kind'] == 'comment'
        assert row['title'] == 'Runbook'
        assert row['body'] == 'Ada: can we drop step 3?'
        assert row['target'] == {'page': ARTIFACTS, 'params': {'slug': 'runbook', 'comment': 'c1'}}

    async def test_a_target_must_be_one_of_the_extensions_pages(self, notify, client, ada):
        response = await notify(
            _note(ada['user_id']),
            _note(ada['user_id'], target={'page': CODEINDEX, 'params': {}}),
        )

        assert response.status_code == 400, response.text
        assert CODEINDEX in response.json()['detail']
        assert await _listed(client, ada) == [], 'a refused batch wrote some of itself'

    async def test_any_of_its_pages_will_do(self, notify, ada):
        response = await notify(_note(ada['user_id'], target={'page': 'igni.artifacts.requests'}))

        assert response.json() == {'delivered': 1}

    async def test_a_notification_need_not_go_anywhere(self, notify, client, ada):
        await notify(_note(ada['user_id'], target=None))

        [row] = await _listed(client, ada)
        assert row['target'] is None

    async def test_a_service_without_an_extension_is_told_to_upload_it(self, client, org, ada):
        headers = await _service(client, org, extension_id=None)

        response = await client.post(
            '/service-notifications', json={'notifications': [_note(ada['user_id'])]}, headers=headers
        )

        assert response.status_code == 409, response.text
        assert 'upload your extension first' in response.json()['detail'].lower()

    async def test_it_needs_a_service_token(self, client, ada):
        """A person's session is not a service acting for an organisation."""
        response = await client.post(
            '/service-notifications', json={'notifications': [_note(ada['user_id'])]}, headers=ada['headers']
        )

        assert response.status_code == 401, response.text


class TestLimits:
    async def test_at_most_a_hundred_notifications_per_call(self, notify, ada):
        response = await notify(*[_note(ada['user_id']) for _ in range(101)])

        assert response.status_code == 413, response.text

    async def test_a_hundred_is_fine(self, notify, ada):
        response = await notify(*[_note(ada['user_id']) for _ in range(100)])

        assert response.json() == {'delivered': 100}

    async def test_at_most_a_thousand_recipients_per_notification(self, notify):
        response = await notify(_note(*[f'u-{n}' for n in range(1001)]))

        assert response.status_code == 413, response.text

    async def test_a_kind_is_a_word_not_an_essay(self, notify, ada):
        response = await notify(_note(ada['user_id'], kind='k' * 65))

        assert response.status_code == 422, response.text

    async def test_a_title_is_required(self, notify, ada):
        response = await notify(_note(ada['user_id'], title=''))

        assert response.status_code == 422, response.text

    async def test_the_body_is_one_line_and_capped(self, notify, client, ada):
        await notify(_note(ada['user_id'], body='first line\nsecond   line ' + 'x' * 400))

        [row] = await _listed(client, ada)
        assert '\n' not in row['body']
        assert row['body'].startswith('first line second line x')
        assert len(row['body']) <= 140


class TestOnThePersonsMachine:
    async def test_listing_marks_delivered(self, notify, client, ada):
        await notify(_note(ada['user_id']))

        [first] = await _listed(client, ada)
        [second] = await _listed(client, ada)

        assert first['delivered_at'] is not None
        assert second['delivered_at'] == first['delivered_at'], 'delivery was re-stamped on a second listing'
        assert first['read_at'] is None

    async def test_newest_first_and_filtered(self, notify, client, org, ada):
        codeindex = await _service(client, org, extension_id=CODEINDEX, pages=(CODEINDEX,))
        await notify(_note(ada['user_id'], title='older'))
        await notify(_note(ada['user_id'], title='index ready', target={'page': CODEINDEX}), headers=codeindex)

        everything = await _listed(client, ada)
        mine = await _listed(client, ada, source=ARTIFACTS)

        assert [row['title'] for row in everything] == ['index ready', 'older']
        assert [row['source'] for row in mine] == [ARTIFACTS]

    async def test_counts_are_unread_per_extension(self, notify, client, org, ada):
        codeindex = await _service(client, org, extension_id=CODEINDEX, pages=(CODEINDEX,))
        await notify(_note(ada['user_id']), _note(ada['user_id']))
        await notify(_note(ada['user_id'], target={'page': CODEINDEX}), headers=codeindex)

        response = await client.get('/notifications/counts', headers=ada['headers'])

        assert response.status_code == 200, response.text
        assert response.json() == {'unread': 3, 'by_source': {ARTIFACTS: 2, CODEINDEX: 1}}

    async def test_mark_read_by_ids(self, notify, client, ada):
        await notify(_note(ada['user_id']), _note(ada['user_id']))
        rows = await _listed(client, ada)

        response = await client.post('/notifications/read', json={'ids': [rows[0]['id']]}, headers=ada['headers'])

        assert response.json() == {'marked': 1, 'unread': 1}
        assert [row['id'] for row in await _listed(client, ada, unread_only=True)] == [rows[1]['id']]

    async def test_mark_read_by_source(self, notify, client, org, ada):
        codeindex = await _service(client, org, extension_id=CODEINDEX, pages=(CODEINDEX,))
        await notify(_note(ada['user_id']), _note(ada['user_id']))
        await notify(_note(ada['user_id'], target={'page': CODEINDEX}), headers=codeindex)

        response = await client.post('/notifications/read', json={'source': ARTIFACTS}, headers=ada['headers'])

        assert response.json() == {'marked': 2, 'unread': 1}
        counts = (await client.get('/notifications/counts', headers=ada['headers'])).json()
        assert counts == {'unread': 1, 'by_source': {CODEINDEX: 1}}

    async def test_mark_everything_read(self, notify, client, ada):
        await notify(_note(ada['user_id']), _note(ada['user_id']))

        response = await client.post('/notifications/read', json={}, headers=ada['headers'])

        assert response.json() == {'marked': 2, 'unread': 0}
        assert all(row['read_at'] for row in await _listed(client, ada))

    async def test_somebody_elses_notification_cannot_be_marked(self, notify, client, ada, max_):
        await notify(_note(ada['user_id']))
        [row] = await _listed(client, ada)

        response = await client.post('/notifications/read', json={'ids': [row['id']]}, headers=max_['headers'])

        assert response.json() == {'marked': 0, 'unread': 0}
        assert (await _listed(client, ada))[0]['read_at'] is None

    async def test_the_list_needs_a_session(self, client):
        assert (await client.get('/notifications')).status_code == 401


class TestTheStream:
    async def test_a_delivery_wakes_the_recipients_stream(self, notify, ada, max_):
        stream = NotificationStream(keepalive_seconds=1)
        with stream.listen(user_id=ada['user_id']) as told, stream.listen(user_id=max_['user_id']) as untold:
            assert 'event: waiting' in told.opening()

            await notify(_note(ada['user_id']))

            frame = await told.waiting()
            assert frame.startswith('event: waiting\nid: 20'), frame
            assert await untold.waiting() == ': keepalive\n\n'

    async def test_a_repeat_wakes_nobody(self, notify, ada):
        note = _note(ada['user_id'], event_key='comment:once')
        await notify(note)
        queue = Listeners().subscribe(ada['user_id'])
        try:
            await notify(note)
            await asyncio.sleep(0)
            assert queue.empty(), 'a notification that was not written still woke somebody'
        finally:
            Listeners().unsubscribe(ada['user_id'], queue)
