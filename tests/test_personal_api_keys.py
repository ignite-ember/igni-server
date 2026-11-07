"""A person's API key: their access, handed to a program.

A person makes a key on the portal; a program exchanges it at the token
endpoint for a 15-minute session as that person, pinned to the
organisation, which every route takes. The key is shown once and only its
hash is kept. It stops at its expiry, when revoked on the portal, and when
its person signs out everywhere, leaves or is deactivated. What it buys
cannot mint further credentials, and every exchange is recorded
(`docs/service-identity.md` §22).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.services.cloud.vm_credentials import VM_SIGN_IN
from app.services.identity.api_keys import API_KEY
from tests.cloud_world import CloudOrg
from tests.conftest import audit_rows, auth_header

pytestmark = pytest.mark.anyio


@pytest.fixture
async def acme(client) -> CloudOrg:
    return await CloudOrg(client).create()


async def _make(client, headers: dict[str, str], name: str = 'nightly review', days: int = 90) -> dict:
    response = await client.post('/portal/api-keys', json={'name': name, 'expires_in_days': days}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _exchange(client, key: str):
    return await client.post('/oauth/token', data={'grant_type': API_KEY, 'api_key': key})


async def _session(client, key: str) -> str:
    response = await _exchange(client, key)
    assert response.status_code == 200, response.text
    return response.json()['access_token']


class TestAKey:
    async def test_it_is_shown_once_and_buys_a_short_session_as_its_person(self, client, acme):
        user_id, headers = await acme.person('ada@acme.com')

        made = await _make(client, headers)
        response = await _exchange(client, made['api_key'])
        [listed] = (await client.get('/portal/api-keys', headers=headers)).json()['keys']

        assert made['api_key'].startswith(f'igpak1.{acme.org_id}.{made["key_id"]}.')
        assert 'api_key' not in listed
        assert listed['name'] == 'nightly review' and listed['user_id'] == user_id
        assert response.status_code == 200, response.text
        body = response.json()
        assert body['expires_in'] == 900 and body['token_type'] == 'Bearer'
        claims = jwt.decode(body['access_token'], options={'verify_signature': False})
        assert claims['sub'] == user_id
        assert claims['org'] == acme.org_id
        assert claims['token_use'] == 'session' and claims['kind'] == 'person'
        assert claims['api_key'] == made['key_id']

    async def test_its_session_reaches_what_its_person_can(self, client, acme):
        _, headers = await acme.person('ada@acme.com')
        session = auth_header(await _session(client, (await _make(client, headers))['api_key']))

        # The portal as the person, their VM's commands, and their extensions —
        # three different auth dependencies, all taking the key's session.
        me = await client.get('/portal/me', headers=session)
        commands = await client.get('/vm/commands', headers=session)
        extensions = await client.get('/extensions', headers=session)

        assert me.status_code == 200, me.text
        assert commands.status_code == 200, commands.text
        assert extensions.status_code == 200, extensions.text

    async def test_its_last_use_is_recorded(self, client, acme):
        _, headers = await acme.person('ada@acme.com')
        made = await _make(client, headers)

        await _session(client, made['api_key'])

        [listed] = (await client.get('/portal/api-keys', headers=headers)).json()['keys']
        assert listed['last_used_at'] is not None

    async def test_garbage_and_a_wrong_secret_are_refused(self, client, acme):
        _, headers = await acme.person('ada@acme.com')
        made = await _make(client, headers)
        forged = made['api_key'].rsplit('.', 1)[0] + '.not-the-secret'

        for key in ('nonsense', 'igpak1.x.y.z', forged):
            assert (await _exchange(client, key)).json()['error'] == 'invalid_grant', key


class TestWhenItStops:
    async def test_at_its_expiry(self, client, acme, monkeypatch):
        _, headers = await acme.person('ada@acme.com')
        made = await _make(client, headers, days=30)
        later = datetime.now(timezone.utc) + timedelta(days=31)
        monkeypatch.setattr(
            'app.services.identity.api_keys.datetime',
            type('Later', (), {'now': staticmethod(lambda tz=None: later)}),
        )

        response = await _exchange(client, made['api_key'])

        assert response.json()['error_description'] == 'this API key has expired'

    async def test_when_its_person_revokes_it(self, client, acme):
        _, headers = await acme.person('ada@acme.com')
        made = await _make(client, headers)

        revoked = await client.delete(f'/portal/api-keys/{made["key_id"]}', headers=headers)

        assert revoked.status_code == 204
        assert (await _exchange(client, made['api_key'])).json()['error_description'] == (
            'this API key has been revoked'
        )
        assert (await client.get('/portal/api-keys', headers=headers)).json()['keys'] == []

    async def test_an_org_admin_sees_and_revokes_everyones_and_a_member_only_their_own(self, client, acme):
        _, ada = await acme.person('ada@acme.com')
        _, max_ = await acme.person('max@acme.com')
        hers = await _make(client, ada)
        await _make(client, max_)

        everyone = (await client.get('/portal/api-keys', headers=acme.admin)).json()['keys']
        max_tries = await client.delete(f'/portal/api-keys/{hers["key_id"]}', headers=max_)
        admin_does = await client.delete(f'/portal/api-keys/{hers["key_id"]}', headers=acme.admin)

        assert len(everyone) == 2
        assert max_tries.status_code == 404
        assert admin_does.status_code == 204
        assert (await _exchange(client, hers['api_key'])).json()['error'] == 'invalid_grant'

    async def test_when_its_person_signs_out_everywhere(self, client, acme):
        _, headers = await acme.person('ada@acme.com')
        made = await _make(client, headers)

        await client.post('/portal/me/revoke-sessions', headers=headers)

        assert (await _exchange(client, made['api_key'])).json()['error'] == 'invalid_grant'

    async def test_when_its_person_leaves_the_organisation(self, client, acme):
        user_id, headers = await acme.person('ada@acme.com')
        made = await _make(client, headers)

        assert (await client.delete(f'/portal/team/{user_id}', headers=acme.admin)).status_code in (200, 204)

        assert (await _exchange(client, made['api_key'])).json()['error'] == 'invalid_grant'

    async def test_when_its_person_is_deactivated(self, client, acme):
        user_id, headers = await acme.person('ada@acme.com')
        made = await _make(client, headers)

        response = await client.patch(
            f'/portal/team/{user_id}/status', json={'status': 'deactivated'}, headers=acme.admin
        )

        assert response.status_code == 200, response.text
        assert (await _exchange(client, made['api_key'])).json()['error'] == 'invalid_grant'


class TestWhatItsSessionCannotDo:
    """A key's session mints nothing longer-lived than itself."""

    async def test_it_makes_lists_and_revokes_no_keys(self, client, acme):
        _, headers = await acme.person('ada@acme.com')
        made = await _make(client, headers)
        session = auth_header(await _session(client, made['api_key']))

        attempts = [
            await client.post('/portal/api-keys', json={'name': 'more'}, headers=session),
            await client.get('/portal/api-keys', headers=session),
            await client.delete(f'/portal/api-keys/{made["key_id"]}', headers=session),
        ]

        assert [a.status_code for a in attempts] == [403, 403, 403]
        assert 'cannot make further credentials' in attempts[0].json()['detail']

    async def test_it_takes_no_cli_session(self, client, acme):
        _, headers = await acme.person('ada@acme.com')
        session = auth_header(await _session(client, (await _make(client, headers))['api_key']))

        response = await client.post('/cli/token', json={'org_id': acme.org_id}, headers=session)

        assert response.status_code == 403

    async def test_it_signs_no_vm_in(self, client, acme):
        _, headers = await acme.person('ada@acme.com')
        session = await _session(client, (await _make(client, headers))['api_key'])

        response = await client.post('/oauth/token', data={'grant_type': VM_SIGN_IN, 'subject_token': session})

        assert response.json()['error_description'] == 'a session from an API key cannot sign a VM in'


class TestTheRecord:
    async def test_making_revoking_and_every_exchange_are_audited(self, client, acme):
        user_id, headers = await acme.person('ada@acme.com')
        made = await _make(client, headers)
        await _session(client, made['api_key'])
        await client.delete(f'/portal/api-keys/{made["key_id"]}', headers=headers)
        await _exchange(client, made['api_key'])  # refused, and still recorded

        rows = audit_rows(target_type='api_key', target_id=made['key_id'])

        routes = sorted((row['method'], row['status_code']) for row in rows)
        assert routes == [('DELETE', 204), ('POST', 200), ('POST', 201), ('POST', 400)]
        exchanged = [row for row in rows if row['status_code'] == 200]
        assert exchanged[0]['actor_user_id'] == user_id
