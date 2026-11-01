"""Each producer's VM holds its own credential; the platform never mints one a token.

A person's cloud igni gets one when they sign in through the gateway; an
app's VM gets one, once, through the node agent's pickup after an org
admin enables the cloud for it. Every exchange rotates it, a reused one
revokes its family, 90 days unused expires it, and sign-out, removal,
deactivation and the portal revoke it (`docs/service-identity.md` §16).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
import jwt
import pytest

from app.config import settings
from app.services.cloud.vm_credentials import VM_CREDENTIAL, VM_SIGN_IN
from tests.cloud_world import CloudOrg, platform
from tests.conftest import audit_rows, auth_header, make_token

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    """An app's first credential is kept encrypted until it is picked up."""
    monkeypatch.setattr(settings, 'secret_encryption_key', Fernet.generate_key().decode())


@pytest.fixture
async def acme(client) -> CloudOrg:
    return await CloudOrg(client).create()


async def _sign_in(client, session: str, **extra):
    return await client.post('/oauth/token', data={'grant_type': VM_SIGN_IN, 'subject_token': session, **extra})


async def _exchange(client, credential: str):
    return await client.post('/oauth/token', data={'grant_type': VM_CREDENTIAL, 'vm_credential': credential})


async def _person(client, acme: CloudOrg, email: str = 'ada@acme.com') -> tuple[str, str, str]:
    """A person signed in through the gateway: their id, session and VM credential."""
    user_id, _ = await acme.person(email)
    session = make_token(user_id, org_id=acme.org_id)
    response = await _sign_in(client, session)
    assert response.status_code == 200, response.text
    return user_id, session, response.json()['vm_credential']


class TestAPersonsVm:
    async def test_signing_in_issues_a_credential_and_a_short_token(self, client, acme):
        user_id, session, _ = await _person(client, acme)

        response = await _sign_in(client, session)

        body = response.json()
        claims = jwt.decode(body['access_token'], options={'verify_signature': False})
        assert body['expires_in'] == 900
        assert body['vm_credential'].startswith(f'igvm1.{acme.org_id}.')
        assert (claims['token_use'], claims['kind'], claims['sub'], claims['org']) == (
            'session',
            'person',
            user_id,
            acme.org_id,
        )
        assert claims['group']

    async def test_an_exchange_rotates_and_the_token_works(self, client, acme):
        _, _, credential = await _person(client, acme)

        first = await _exchange(client, credential)
        second = await _exchange(client, first.json()['vm_credential'])

        assert first.status_code == 200, first.text
        assert first.json()['vm_credential'] != credential
        assert second.status_code == 200
        listing = await client.get('/extensions', headers=auth_header(second.json()['access_token']))
        assert listing.status_code == 200
        assert listing.json()['producer']['kind'] == 'person'

    async def test_reusing_an_older_one_revokes_the_family(self, client, acme):
        _, _, credential = await _person(client, acme)
        newer = (await _exchange(client, credential)).json()['vm_credential']
        newest = (await _exchange(client, newer)).json()['vm_credential']

        reused = await _exchange(client, credential)
        after = await _exchange(client, newest)

        assert reused.json()['error'] == 'invalid_grant'
        assert 'already used' in reused.json()['error_description']
        assert after.json()['error'] == 'invalid_grant'


class TestALostReply:
    """For 60 s after a rotation the credential it replaced may be presented
    once more, and gets the same successor — an idempotent retry."""

    async def test_a_retry_gets_the_same_successor_once(self, client, acme):
        _, _, credential = await _person(client, acme)
        first = (await _exchange(client, credential)).json()

        retry = await _exchange(client, credential)

        assert retry.status_code == 200, retry.text
        assert retry.json()['vm_credential'] == first['vm_credential']
        assert retry.json()['access_token']
        assert (await _exchange(client, first['vm_credential'])).status_code == 200

    async def test_a_second_retry_revokes_the_family(self, client, acme):
        _, _, credential = await _person(client, acme)
        successor = (await _exchange(client, credential)).json()['vm_credential']
        await _exchange(client, credential)

        again = await _exchange(client, credential)

        assert again.json()['error'] == 'invalid_grant'
        assert (await _exchange(client, successor)).json()['error'] == 'invalid_grant'

    async def test_a_retry_after_sixty_seconds_revokes_the_family(self, client, acme, monkeypatch):
        _, _, credential = await _person(client, acme)
        successor = (await _exchange(client, credential)).json()['vm_credential']
        later = datetime.now(timezone.utc) + timedelta(seconds=61)
        monkeypatch.setattr(
            'app.db.sql.vm_credentials.datetime',
            type('Later', (), {'now': staticmethod(lambda tz=None: later)}),
        )

        late = await _exchange(client, credential)

        assert late.json()['error'] == 'invalid_grant'
        assert (await _exchange(client, successor)).json()['error'] == 'invalid_grant'

    async def test_two_exchanges_at_once_get_one_successor(self, client, acme):
        _, _, credential = await _person(client, acme)

        one, two = await asyncio.gather(_exchange(client, credential), _exchange(client, credential))
        third = await _exchange(client, credential)

        assert (one.status_code, two.status_code) == (200, 200)
        assert one.json()['vm_credential'] == two.json()['vm_credential']
        assert third.json()['error'] == 'invalid_grant'

    async def test_a_retry_survives_a_key_rotation(self, client, acme, monkeypatch):
        _, _, credential = await _person(client, acme)
        old_key = settings.secret_encryption_key
        successor = (await _exchange(client, credential)).json()['vm_credential']
        monkeypatch.setattr(settings, 'secret_encryption_key', Fernet.generate_key().decode())
        monkeypatch.setattr(settings, 'secret_encryption_keys_previous', old_key)

        retry = await _exchange(client, credential)

        assert retry.json()['vm_credential'] == successor

    async def test_signing_in_again_replaces_the_old_family(self, client, acme):
        _, session, old = await _person(client, acme)

        await _sign_in(client, session)

        assert (await _exchange(client, old)).json()['error'] == 'invalid_grant'

    async def test_signing_out_revokes_it(self, client, acme):
        _, session, credential = await _person(client, acme)

        await client.post('/portal/me/revoke-sessions', headers=auth_header(session))

        assert (await _exchange(client, credential)).json()['error'] == 'invalid_grant'

    async def test_removal_from_the_organisation_revokes_it(self, client, acme):
        user_id, _, credential = await _person(client, acme)

        assert (await client.delete(f'/portal/team/{user_id}', headers=acme.admin)).status_code in (200, 204)

        assert (await _exchange(client, credential)).json()['error'] == 'invalid_grant'

    async def test_deactivation_revokes_it(self, client, acme):
        user_id, _, credential = await _person(client, acme)

        response = await client.patch(
            f'/portal/team/{user_id}/status', json={'status': 'deactivated'}, headers=acme.admin
        )

        assert response.status_code == 200, response.text
        assert (await _exchange(client, credential)).json()['error'] == 'invalid_grant'

    async def test_ninety_days_unused_expires_it(self, client, acme, monkeypatch):
        _, _, credential = await _person(client, acme)
        later = datetime.now(timezone.utc) + timedelta(days=91)
        monkeypatch.setattr(
            'app.services.cloud.vm_credentials.datetime',
            type('Later', (), {'now': staticmethod(lambda tz=None: later)}),
        )

        response = await _exchange(client, credential)

        assert '90 days' in response.json()['error_description']

    async def test_the_person_lists_and_revokes_their_own(self, client, acme):
        user_id, session, credential = await _person(client, acme)
        headers = auth_header(session)

        [listed] = (await client.get('/portal/vm-credentials', headers=headers)).json()['credentials']
        revoked = await client.delete(f'/portal/vm-credentials/{listed["credential_id"]}', headers=headers)

        assert listed['producer'] == {'kind': 'person', 'id': user_id}
        assert 'vm_credential' not in listed
        assert revoked.status_code == 204
        assert (await _exchange(client, credential)).json()['error'] == 'invalid_grant'

    async def test_an_org_admin_sees_and_revokes_everyones_and_a_member_only_their_own(self, client, acme):
        _, ada_session, ada_credential = await _person(client, acme, 'ada@acme.com')
        _, max_session, _ = await _person(client, acme, 'max@acme.com')

        everyone = (await client.get('/portal/vm-credentials', headers=acme.admin)).json()['credentials']
        hers = (await client.get('/portal/vm-credentials', headers=auth_header(ada_session))).json()['credentials']
        max_tries = await client.delete(
            f'/portal/vm-credentials/{hers[0]["credential_id"]}', headers=auth_header(max_session)
        )
        admin_does = await client.delete(f'/portal/vm-credentials/{hers[0]["credential_id"]}', headers=acme.admin)

        assert len(everyone) == 2
        assert len(hers) == 1
        assert max_tries.status_code == 404
        assert admin_does.status_code == 204
        assert (await _exchange(client, ada_credential)).json()['error'] == 'invalid_grant'

    async def test_garbage_is_refused(self, client):
        assert (await _exchange(client, 'igvm1.x.y.0.z')).json()['error'] == 'invalid_grant'
        assert (await _exchange(client, 'nonsense')).json()['error'] == 'invalid_grant'


class TestAnAppsVm:
    async def test_issued_on_enabling_picked_up_once_then_lives_on_the_vm(self, client, acme):
        app = await acme.app()
        gateway = await platform(client)

        enabled = await client.post(f'/portal/apps/{app.client_id}/vm-credential', headers=acme.admin)
        pickup = f'/platform/orgs/{acme.org_id}/producers/app/{app.client_id}/vm-credential/pickup'
        first = await client.post(pickup, headers=gateway)
        second = await client.post(pickup, headers=gateway)
        exchanged = await _exchange(client, first.json()['vm_credential'])

        assert enabled.status_code == 201
        assert enabled.json()['awaiting_pickup'] is True
        assert 'vm_credential' not in enabled.json()
        assert first.status_code == 200
        assert second.status_code == 404
        claims = jwt.decode(exchanged.json()['access_token'], options={'verify_signature': False})
        assert (claims['token_use'], claims['kind'], claims['sub']) == ('app', 'app', app.client_id)
        assert 'group' not in claims
        listed = (await client.get('/portal/vm-credentials', headers=acme.admin)).json()['credentials']
        assert [(c['awaiting_pickup'], c['picked_up_at'] is not None) for c in listed] == [(False, True)]

    async def test_reissuing_invalidates_the_old_family(self, client, acme):
        app = await acme.app()
        gateway = await platform(client)
        await client.post(f'/portal/apps/{app.client_id}/vm-credential', headers=acme.admin)
        pickup = f'/platform/orgs/{acme.org_id}/producers/app/{app.client_id}/vm-credential/pickup'
        old = (await client.post(pickup, headers=gateway)).json()['vm_credential']

        await client.post(f'/portal/apps/{app.client_id}/vm-credential', headers=acme.admin)
        new = await client.post(pickup, headers=gateway)

        assert (await _exchange(client, old)).json()['error'] == 'invalid_grant'
        assert (await _exchange(client, new.json()['vm_credential'])).status_code == 200

    async def test_removing_the_app_revokes_it(self, client, acme):
        app = await acme.app()
        gateway = await platform(client)
        await client.post(f'/portal/apps/{app.client_id}/vm-credential', headers=acme.admin)
        credential = (
            await client.post(
                f'/platform/orgs/{acme.org_id}/producers/app/{app.client_id}/vm-credential/pickup', headers=gateway
            )
        ).json()['vm_credential']

        await client.delete(f'/portal/apps/{app.client_id}', headers=acme.admin)

        assert (await _exchange(client, credential)).json()['error'] == 'invalid_grant'

    async def test_only_an_org_admin_enables_it(self, client, acme):
        app = await acme.app()
        _, member = await acme.person('max@acme.com')

        assert (await client.post(f'/portal/apps/{app.client_id}/vm-credential', headers=member)).status_code == 403

    async def test_the_pickup_is_audited(self, client, acme):
        app = await acme.app()
        gateway = await platform(client)
        await client.post(f'/portal/apps/{app.client_id}/vm-credential', headers=acme.admin)

        await client.post(
            f'/platform/orgs/{acme.org_id}/producers/app/{app.client_id}/vm-credential/pickup', headers=gateway
        )

        assert any(row['route'].endswith('/vm-credential/pickup') for row in audit_rows())


class TestNoPlatformTokenForAProducer:
    async def test_a_platform_token_is_not_a_sign_in(self, client, acme):
        gateway = await platform(client)

        response = await _sign_in(client, gateway['Authorization'].split()[1], organisation=acme.org_id)

        assert response.json()['error'] == 'invalid_grant'

    async def test_a_platform_token_cannot_be_exchanged_for_a_person(self, client, acme):
        gateway = await platform(client)
        response = await client.post(
            '/oauth/token',
            data={
                'grant_type': 'urn:ietf:params:oauth:grant-type:token-exchange',
                'subject_token': gateway['Authorization'].split()[1],
                'subject_token_type': 'urn:ietf:params:oauth:token-type:access_token',
                'audience': 'https://codeindex.example.com',
                'organisation': acme.org_id,
            },
        )

        assert response.json()['error'] == 'invalid_grant'

    async def test_the_pickup_is_no_way_to_a_persons_credential(self, client, acme):
        user_id, _, _ = await _person(client, acme)
        gateway = await platform(client)

        response = await client.post(
            f'/platform/orgs/{acme.org_id}/producers/person/{user_id}/vm-credential/pickup', headers=gateway
        )

        assert response.status_code in (404, 405)

    async def test_no_platform_route_issues_a_token(self, app):
        """Every /v1/platform route, read from the router: none is a token or credential issuer but the pickup."""
        paths = [path for path in app.openapi()['paths'] if '/platform/' in path]

        assert [p for p in paths if 'token' in p] == []
        assert [p for p in paths if 'credential' in p] == [
            '/v1/platform/orgs/{org_id}/producers/{kind}/{producer_id}/vm-credential/pickup'
        ]
