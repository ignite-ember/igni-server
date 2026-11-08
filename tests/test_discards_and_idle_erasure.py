"""Discarding what is no longer wanted, and erasing an app's VM after inactivity.

* **An app is discarded** at once: never woken again, its credential
  refused, its place under the licence free, listed removed on the feed
  with its erasure due now — unless a legal hold on it stands, which keeps
  its data (the app is still gone). It may be replaced at once by a fresh
  app with the same name, key and settings.
* **A removed member's VM is discarded now** instead of at the end of the
  retention; a current member's VM never is, and a legal hold refuses it.
* **An app's VM not woken for its profile's `erase_after_idle_days`** is
  erased by the gateway (it asks first, then reports); never a person's,
  never with a legal hold, never when unset; bounded by the platform's
  maximum.
* The gateway **reports each erasure done**; the discard and the
  completion are both in the organisation's audit trail, and the portal
  shows erasing → erased (`docs/service-identity.md` §12.2, §18).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
import pytest

from app.config import settings
from app.services.cloud.vm_credential_grants import VM_CREDENTIAL
from app.services.cloud.vm_profiles import ProfileVersion, VmProfiles
from tests.cloud_world import CloudOrg, make_super_admin, platform
from tests.conftest import audit_rows

pytestmark = pytest.mark.anyio

ERASED_AFTER_30 = {'name': 'Quarterly', 'erase_after_idle_days': 30}


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    """An app's credential is kept encrypted until it is picked up."""
    monkeypatch.setattr(settings, 'secret_encryption_key', Fernet.generate_key().decode())


@pytest.fixture
async def acme(client) -> CloudOrg:
    return await CloudOrg(client).create()


@pytest.fixture
async def gateway(client) -> dict[str, str]:
    return await platform(client)


def _profiles(acme: CloudOrg) -> str:
    return f'/portal/orgs/{acme.org_id}/vm-profiles'


async def _create(client, acme: CloudOrg, body: dict) -> dict:
    response = await client.post(_profiles(acme), json=body, headers=acme.admin)
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _feed_rows(client, gateway, kind: str, producer_id: str) -> list[dict]:
    response = await client.get('/platform/producers', params={'limit': 1000}, headers=gateway)
    assert response.status_code == 200, response.text
    return [row for row in response.json()['producers'] if row['producer'] == {'kind': kind, 'id': producer_id}]


async def _caps(client, super_admin: dict[str, str], **values) -> dict:
    current = (await client.get('/portal/vm-caps', headers=super_admin)).json()
    body = {k: current[k] for k in ('max_memory_mib', 'max_vcpus', 'max_idle_before_pause_s', 'max_volume_gib')}
    response = await client.put('/portal/vm-caps', json={**body, **values}, headers=super_admin)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _super_admin(acme: CloudOrg) -> dict[str, str]:
    await make_super_admin(acme.admin_id)
    return acme.admin


async def _person_with(client, acme: CloudOrg, profile: dict, email: str = 'ada@acme.com') -> str:
    group = await acme.group(f'Group of {email}')
    assigned = await client.put(
        f'/portal/orgs/{acme.org_id}/groups/{group}/vm-profile', json={'profile_id': profile['id']}, headers=acme.admin
    )
    assert assigned.status_code == 204, assigned.text
    user_id, _ = await acme.person(email, group)
    return user_id


async def _app_with(client, acme: CloudOrg, gateway, profile: dict | None = None, *, cloud: bool = True):
    """An app on *profile*, its cloud enabled and its first credential picked up by the node agent."""
    app = await acme.app()
    if profile is not None:
        assigned = await client.put(
            f'/portal/orgs/{acme.org_id}/apps/{app.client_id}/vm-profile',
            json={'profile_id': profile['id']},
            headers=acme.admin,
        )
        assert assigned.status_code == 204, assigned.text
    credential = None
    if cloud:
        await client.post(f'/portal/apps/{app.client_id}/vm-credential', headers=acme.admin)
        picked = await client.post(_pickup(acme, app.client_id), headers=gateway)
        assert picked.status_code == 200, picked.text
        credential = picked.json()['vm_credential']
    await _feed_rows(client, gateway, 'app', app.client_id)
    return app, credential


def _pickup(acme: CloudOrg, app_id: str) -> str:
    return f'/platform/orgs/{acme.org_id}/producers/app/{app_id}/vm-credential/pickup'


def _resets(org_id: str, kind: str, producer_id: str) -> str:
    return f'/platform/orgs/{org_id}/producers/{kind}/{producer_id}/vm-resets'


async def _exchange(client, credential: str):
    return await client.post('/oauth/token', data={'grant_type': VM_CREDENTIAL, 'vm_credential': credential})


async def _erasures(client, acme: CloudOrg) -> list[dict]:
    response = await client.get(f'/portal/orgs/{acme.org_id}/erasures', headers=acme.admin)
    assert response.status_code == 200, response.text
    return list(response.json()['erasures'])


async def _removed(client, acme: CloudOrg, gateway, email: str = 'ada@acme.com') -> tuple[str, str]:
    """A member removed from the organisation: their id and the incarnation now waiting for erasure."""
    ada, _ = await acme.person(email)
    incarnation = (await _feed_rows(client, gateway, 'person', ada))[-1]['incarnation']
    assert (await client.delete(f'/portal/team/{ada}', headers=acme.admin)).status_code in (200, 204)
    await _feed_rows(client, gateway, 'person', ada)
    return ada, incarnation


class TestTheProfileSays:
    async def test_by_default_an_apps_vm_is_never_erased_for_inactivity(self, client, acme):
        [standard] = (await client.get(_profiles(acme), headers=acme.admin)).json()['profiles']

        assert standard['erase_after_idle_days'] is None

    async def test_an_org_admin_has_it_erased_after_a_month(self, client, acme):
        quarterly = await _create(client, acme, ERASED_AFTER_30)

        assert quarterly['erase_after_idle_days'] == 30

    @pytest.mark.parametrize('days', [0, -1, 4000])
    async def test_days_out_of_range_are_refused(self, client, acme, days):
        response = await client.post(
            _profiles(acme), json={**ERASED_AFTER_30, 'erase_after_idle_days': days}, headers=acme.admin
        )

        assert response.status_code == 422

    async def test_unset_it_leaves_every_version_as_it_was(self, client, acme):
        """Adding the field must not change every producer's version: the gateway would re-provision every VM.

        `internet` came later still (t0026) and is left out the same way."""
        [standard] = (await client.get(_profiles(acme), headers=acme.admin)).json()['profiles']
        settings_now = VmProfiles.settings_of(await VmProfiles().default(acme.org_id))
        before = ProfileVersion.digest(
            {
                'id': standard['id'],
                **settings_now.model_dump(mode='json', exclude={'erase_after_idle_days', 'internet'}),
            }
        )

        assert standard['version'] == before


class TestThePlatformMaximum:
    async def test_without_one_never_erasing_is_allowed(self, client, acme):
        caps = (await client.get('/portal/vm-caps', headers=acme.admin)).json()

        assert caps['max_erase_after_idle_days'] is None
        await _create(client, acme, {'name': 'Kept'})

    async def test_with_one_a_profile_over_it_or_never_erasing_is_refused(self, client, acme):
        await _caps(client, await _super_admin(acme), max_erase_after_idle_days=30)

        over = await client.post(
            _profiles(acme), json={**ERASED_AFTER_30, 'erase_after_idle_days': 31}, headers=acme.admin
        )
        never = await client.post(_profiles(acme), json={'name': 'Kept'}, headers=acme.admin)
        within = await client.post(_profiles(acme), json=ERASED_AFTER_30, headers=acme.admin)

        assert over.status_code == 400
        assert '30 days' in over.json()['detail']
        assert never.status_code == 400
        assert within.status_code == 201

    async def test_only_a_super_admin_sets_it(self, client, acme):
        response = await client.put(
            '/portal/vm-caps',
            json={'max_memory_mib': 8192, 'max_vcpus': 4, 'max_volume_gib': 200, 'max_erase_after_idle_days': 7},
            headers=acme.admin,
        )

        assert response.status_code == 403

    async def test_lowered_it_clamps_what_was_saved_and_the_feed_serves_it_clamped(self, client, acme, gateway):
        quarterly = await _create(client, acme, ERASED_AFTER_30)
        app, _ = await _app_with(client, acme, gateway, quarterly)

        await _caps(client, await _super_admin(acme), max_erase_after_idle_days=7)

        profiles = {p['name']: p for p in (await client.get(_profiles(acme), headers=acme.admin)).json()['profiles']}
        assert (profiles['Quarterly']['erase_after_idle_days'], profiles['Quarterly']['within_caps']) == (30, False)
        assert (await _feed_rows(client, gateway, 'app', app.client_id))[-1]['profile']['erase_after_idle_days'] == 7

    async def test_out_of_range_is_refused(self, client, acme):
        response = await client.put(
            '/portal/vm-caps',
            json={'max_memory_mib': 8192, 'max_vcpus': 4, 'max_volume_gib': 200, 'max_erase_after_idle_days': 0},
            headers=await _super_admin(acme),
        )

        assert response.status_code == 422


class TestTheFeedCarriesIt:
    async def test_the_profile_on_the_feed_says_how_many_days(self, client, acme, gateway):
        app, _ = await _app_with(client, acme, gateway, await _create(client, acme, ERASED_AFTER_30))

        row = (await _feed_rows(client, gateway, 'app', app.client_id))[-1]

        assert row['profile']['erase_after_idle_days'] == 30
        assert row['legal_hold'] is False

    async def test_a_legal_hold_on_an_app_is_on_its_live_row_until_released(self, client, acme, gateway):
        app, _ = await _app_with(client, acme, gateway)
        holds = f'/portal/orgs/{acme.org_id}/legal-holds/apps/{app.client_id}'

        placed = await client.put(holds, json={'reason': 'Litigation 2026-17'}, headers=acme.admin)
        held = (await _feed_rows(client, gateway, 'app', app.client_id))[-1]
        listed = (await client.get(f'/portal/orgs/{acme.org_id}/legal-holds', headers=acme.admin)).json()
        released = await client.delete(holds, headers=acme.admin)
        free = (await _feed_rows(client, gateway, 'app', app.client_id))[-1]

        assert placed.status_code == 200, placed.text
        assert placed.json()['reason'] == 'Litigation 2026-17'
        assert [h['app_id'] for h in listed['apps']] == [app.client_id]
        assert released.status_code == 204
        assert (held['legal_hold'], held['removed']) == (True, False)
        assert free['legal_hold'] is False

    async def test_a_legal_hold_on_a_person_is_on_their_live_row(self, client, acme, gateway):
        ada, _ = await acme.person('ada@acme.com')

        await client.put(f'/portal/orgs/{acme.org_id}/legal-holds/{ada}', json={'reason': 'R'}, headers=acme.admin)

        assert (await _feed_rows(client, gateway, 'person', ada))[-1]['legal_hold'] is True

    async def test_a_hold_on_an_unknown_app_is_refused(self, client, acme):
        response = await client.put(
            f'/portal/orgs/{acme.org_id}/legal-holds/apps/no-such-app', json={'reason': 'R'}, headers=acme.admin
        )

        assert response.status_code == 404


class TestErasureAfterInactivity:
    async def test_the_gateway_may_erase_an_apps_vm_whose_profile_says_so(self, client, acme, gateway):
        app, _ = await _app_with(client, acme, gateway, await _create(client, acme, ERASED_AFTER_30))
        body = {'incarnation': app.client_id, 'reason': 'idle'}

        response = await client.post(_resets(acme.org_id, 'app', app.client_id) + '/check', json=body, headers=gateway)

        assert response.status_code == 204

    async def test_never_a_members_vm(self, client, acme, gateway):
        ada = await _person_with(client, acme, await _create(client, acme, ERASED_AFTER_30))
        body = {'incarnation': (await _feed_rows(client, gateway, 'person', ada))[-1]['incarnation'], 'reason': 'idle'}

        check = await client.post(_resets(acme.org_id, 'person', ada) + '/check', json=body, headers=gateway)
        report = await client.post(_resets(acme.org_id, 'person', ada), json=body, headers=gateway)

        assert check.status_code == 409
        assert 'member' in check.json()['detail']
        assert report.status_code == 409
        assert [e for e in audit_rows(route='producer-vm/reset') if e['target_id'] == ada] == []

    async def test_never_when_unset(self, client, acme, gateway):
        app, _ = await _app_with(client, acme, gateway, await _create(client, acme, {'name': 'Kept'}))
        body = {'incarnation': app.client_id, 'reason': 'idle'}

        check = await client.post(_resets(acme.org_id, 'app', app.client_id) + '/check', json=body, headers=gateway)
        report = await client.post(_resets(acme.org_id, 'app', app.client_id), json=body, headers=gateway)

        assert check.status_code == 409
        assert report.status_code == 409
        assert [e for e in audit_rows(route='producer-vm/reset') if e['target_id'] == app.client_id] == []

    async def test_never_with_a_legal_hold_on_the_app(self, client, acme, gateway):
        app, _ = await _app_with(client, acme, gateway, await _create(client, acme, ERASED_AFTER_30))
        body = {'incarnation': app.client_id, 'reason': 'idle'}
        holds = f'/portal/orgs/{acme.org_id}/legal-holds/apps/{app.client_id}'
        await client.put(holds, json={'reason': 'Regulator request'}, headers=acme.admin)

        check = await client.post(_resets(acme.org_id, 'app', app.client_id) + '/check', json=body, headers=gateway)
        report = await client.post(_resets(acme.org_id, 'app', app.client_id), json=body, headers=gateway)
        await client.delete(holds, headers=acme.admin)
        after_release = await client.post(
            _resets(acme.org_id, 'app', app.client_id) + '/check', json=body, headers=gateway
        )

        assert check.status_code == 409
        assert 'legal hold' in check.json()['detail']
        assert 'Regulator request' in check.json()['detail']
        assert report.status_code == 409
        assert [e for e in audit_rows(route='producer-vm/reset') if e['target_id'] == app.client_id] == []
        assert after_release.status_code == 204

    async def test_never_for_another_life(self, client, acme, gateway):
        app, _ = await _app_with(client, acme, gateway, await _create(client, acme, ERASED_AFTER_30))

        response = await client.post(
            _resets(acme.org_id, 'app', app.client_id) + '/check',
            json={'incarnation': 'an-earlier-app', 'reason': 'idle'},
            headers=gateway,
        )

        assert response.status_code == 409

    async def test_an_erasure_is_audited_and_the_next_vm_still_authenticates(self, client, acme, gateway):
        app, old = await _app_with(client, acme, gateway, await _create(client, acme, ERASED_AFTER_30))

        reported = await client.post(
            _resets(acme.org_id, 'app', app.client_id),
            json={
                'incarnation': app.client_id,
                'reason': 'idle',
                'last_woken_at': '2026-09-01T09:00:00Z',
                'summary': {'state': 'erased', 'volume': 'erased', 'keys': 'erased'},
            },
            headers=gateway,
        )
        again = await client.post(_pickup(acme, app.client_id), headers=gateway)

        assert reported.status_code == 200, reported.text
        assert reported.json()['credential'] == 'reissued'
        assert again.status_code == 200
        assert (await _exchange(client, again.json()['vm_credential'])).status_code == 200
        assert (await _exchange(client, old)).json()['error'] == 'invalid_grant'
        [event] = [e for e in audit_rows(route='producer-vm/reset') if e['target_id'] == app.client_id]
        assert (event['org_id'], event['target_type'], event['method']) == (acme.org_id, 'app_vm', 'SYSTEM')
        assert event['detail']['why'] == 'erased_after_idle'
        assert event['detail']['erase_after_idle_days'] == 30
        assert event['detail']['last_woken_at'].startswith('2026-09-01T09:00:00')
        assert event['detail']['summary']['volume'] == 'erased'

    async def test_a_credential_still_waiting_is_left_waiting(self, client, acme, gateway):
        app = await acme.app()
        await client.put(
            f'/portal/orgs/{acme.org_id}/apps/{app.client_id}/vm-profile',
            json={'profile_id': (await _create(client, acme, ERASED_AFTER_30))['id']},
            headers=acme.admin,
        )
        await client.post(f'/portal/apps/{app.client_id}/vm-credential', headers=acme.admin)

        response = await client.post(
            _resets(acme.org_id, 'app', app.client_id), json={'incarnation': app.client_id}, headers=gateway
        )

        assert response.json()['credential'] == 'awaiting_pickup'

    async def test_only_a_platform_client_reports_one(self, client, acme, gateway):
        app, _ = await _app_with(client, acme, gateway, await _create(client, acme, ERASED_AFTER_30))

        response = await client.post(
            _resets(acme.org_id, 'app', app.client_id), json={'incarnation': app.client_id}, headers=acme.admin
        )

        assert response.status_code in (401, 403)

    async def test_an_oversized_summary_is_refused(self, client, acme, gateway):
        app, _ = await _app_with(client, acme, gateway, await _create(client, acme, ERASED_AFTER_30))

        response = await client.post(
            _resets(acme.org_id, 'app', app.client_id),
            json={'incarnation': app.client_id, 'summary': {'x': 'y' * 9000}},
            headers=gateway,
        )

        assert response.status_code == 422


class TestDiscardingAnApp:
    async def test_it_is_gone_at_once_and_its_erasure_due_now(self, client, acme, gateway):
        app, credential = await _app_with(client, acme, gateway)

        discarded = await client.post(f'/portal/apps/{app.client_id}/discard', json={}, headers=acme.admin)
        row = (await _feed_rows(client, gateway, 'app', app.client_id))[-1]

        assert discarded.status_code == 200, discarded.text
        body = discarded.json()
        assert body['fresh_app'] is None
        assert (body['discarded']['state'], body['discarded']['name']) == ('erasing', 'Review bot')
        assert body['discarded']['discarded_by_user_id'] == acme.admin_id
        assert (await client.get(f'/portal/apps/{app.client_id}', headers=acme.admin)).status_code == 404
        assert (await _exchange(client, credential)).json()['error'] == 'invalid_grant'
        assert (row['removed'], row['active']) == (True, False)
        assert datetime.fromisoformat(row['erase_at']) <= datetime.now(timezone.utc)
        assert (await client.post('/oauth/token', data=app.authenticated('client_credentials'))).status_code != 200

    async def test_the_discard_and_its_erasure_are_audited_and_shown(self, client, acme, gateway):
        app, _ = await _app_with(client, acme, gateway)
        await client.post(f'/portal/apps/{app.client_id}/discard', json={}, headers=acme.admin)
        incarnation = app.client_id

        done = await client.post(
            f'/platform/orgs/{acme.org_id}/producers/app/{app.client_id}/erasures',
            json={'incarnation': incarnation, 'summary': {'state': 'erased', 'volume': 'erased', 'keys': 'erased'}},
            headers=gateway,
        )
        again = await client.post(
            f'/platform/orgs/{acme.org_id}/producers/app/{app.client_id}/erasures',
            json={'incarnation': incarnation},
            headers=gateway,
        )
        [shown] = [e for e in await _erasures(client, acme) if e['producer']['id'] == app.client_id]

        assert done.status_code == 200, done.text
        assert done.json()['state'] == 'erased'
        assert again.status_code == 200
        assert (shown['state'], shown['name'], shown['erased_at'] is not None) == ('erased', 'Review bot', True)
        assert any(
            row['route'].endswith('/apps/{app_id}/discard') and row['target_id'] == app.client_id
            for row in audit_rows()
        )
        [completed] = [
            e
            for e in audit_rows(route='producer-feed/erasure')
            if e['target_id'] == app.client_id and e['detail']['why'] == 'erased'
        ]
        assert completed['org_id'] == acme.org_id
        assert completed['detail']['summary']['volume'] == 'erased'

    async def test_an_unknown_erasure_is_a_404(self, client, acme, gateway):
        response = await client.post(
            f'/platform/orgs/{acme.org_id}/producers/app/nothing/erasures', json={'incarnation': 'x'}, headers=gateway
        )

        assert response.status_code == 404

    async def test_a_legal_hold_keeps_its_data_but_the_app_is_still_gone(self, client, acme, gateway):
        app, credential = await _app_with(client, acme, gateway)
        holds = f'/portal/orgs/{acme.org_id}/legal-holds/apps/{app.client_id}'
        await client.put(holds, json={'reason': 'Litigation'}, headers=acme.admin)

        discarded = await client.post(f'/portal/apps/{app.client_id}/discard', json={}, headers=acme.admin)
        row = (await _feed_rows(client, gateway, 'app', app.client_id))[-1]

        assert discarded.json()['discarded']['state'] == 'held'
        assert (row['removed'], row['erase_at']) == (True, None)
        assert (await client.get(f'/portal/apps/{app.client_id}', headers=acme.admin)).status_code == 404
        assert (await _exchange(client, credential)).json()['error'] == 'invalid_grant'

    async def test_released_the_hold_on_a_discarded_app_erases_it_now(self, client, acme, gateway):
        app, _ = await _app_with(client, acme, gateway)
        holds = f'/portal/orgs/{acme.org_id}/legal-holds/apps/{app.client_id}'
        await client.put(holds, json={'reason': 'Litigation'}, headers=acme.admin)
        await client.post(f'/portal/apps/{app.client_id}/discard', json={}, headers=acme.admin)

        released = await client.delete(holds, headers=acme.admin)
        row = (await _feed_rows(client, gateway, 'app', app.client_id))[-1]

        assert released.status_code == 204
        assert row['erase_at'] is not None
        assert datetime.fromisoformat(row['erase_at']) <= datetime.now(timezone.utc) + timedelta(seconds=5)

    async def test_start_fresh_registers_the_same_app_again_with_a_new_vm(self, client, acme, gateway):
        profile = await _create(client, acme, ERASED_AFTER_30)
        app, _ = await _app_with(client, acme, gateway, profile)
        old = (await client.get(f'/portal/apps/{app.client_id}', headers=acme.admin)).json()

        discarded = await client.post(
            f'/portal/apps/{app.client_id}/discard', json={'start_fresh': True}, headers=acme.admin
        )
        fresh = discarded.json()['fresh_app']
        picked = await client.post(_pickup(acme, fresh['app_id']), headers=gateway)
        token = await client.post('/oauth/token', data={**app.authenticated('client_credentials')})

        assert discarded.status_code == 200, discarded.text
        assert fresh['app_id'] != app.client_id
        assert (fresh['name'], fresh['thumbprint'], fresh['owner_user_id']) == (
            old['name'],
            old['thumbprint'],
            old['owner_user_id'],
        )
        assert fresh['vm_profile_id'] == profile['id']
        assert picked.status_code == 200, picked.text
        assert token.status_code != 200  # the old client id is gone; the app now signs as the new one
        new_row = (await _feed_rows(client, gateway, 'app', fresh['app_id']))[-1]
        assert (new_row['active'], new_row['removed']) == (True, False)

    async def test_only_an_org_admin_discards(self, client, acme, gateway):
        app, _ = await _app_with(client, acme, gateway)
        _, member = await acme.person('max@acme.com')

        response = await client.post(f'/portal/apps/{app.client_id}/discard', json={}, headers=member)

        assert response.status_code == 403

    async def test_deleting_an_app_is_discarding_it(self, client, acme, gateway):
        app, _ = await _app_with(client, acme, gateway)

        assert (await client.delete(f'/portal/apps/{app.client_id}', headers=acme.admin)).status_code == 204
        [shown] = [e for e in await _erasures(client, acme) if e['producer']['id'] == app.client_id]
        assert (shown['state'], shown['name']) == ('erasing', 'Review bot')


class TestDiscardingARemovedMember:
    async def test_a_current_members_vm_is_never_discarded(self, client, acme, gateway):
        ada, _ = await acme.person('ada@acme.com')
        await _feed_rows(client, gateway, 'person', ada)

        response = await client.post(f'/portal/orgs/{acme.org_id}/members/{ada}/discard', json={}, headers=acme.admin)

        assert response.status_code == 409
        assert 'current member' in response.json()['detail']

    async def test_after_removal_it_is_erased_now_not_after_the_retention(self, client, acme, gateway):
        ada, incarnation = await _removed(client, acme, gateway)
        before = (await _feed_rows(client, gateway, 'person', ada))[-1]

        response = await client.post(
            f'/portal/orgs/{acme.org_id}/members/{ada}/discard', json={'incarnation': incarnation}, headers=acme.admin
        )
        after = (await _feed_rows(client, gateway, 'person', ada))[-1]

        assert datetime.fromisoformat(before['erase_at']) > datetime.now(timezone.utc) + timedelta(days=29)
        assert response.status_code == 200, response.text
        [erasure] = response.json()['erasures']
        assert (erasure['state'], erasure['name'], erasure['incarnation']) == ('erasing', 'ada@acme.com', incarnation)
        assert erasure['discarded_by_user_id'] == acme.admin_id
        assert datetime.fromisoformat(after['erase_at']) <= datetime.now(timezone.utc)
        whys = [e['detail']['why'] for e in audit_rows(route='producer-feed/erasure') if e['target_id'] == ada]
        assert 'discarded' in whys

    async def test_a_legal_hold_refuses_it_and_says_so(self, client, acme, gateway):
        ada, _ = await _removed(client, acme, gateway)
        await client.put(
            f'/portal/orgs/{acme.org_id}/legal-holds/{ada}', json={'reason': 'Case 42'}, headers=acme.admin
        )

        response = await client.post(f'/portal/orgs/{acme.org_id}/members/{ada}/discard', json={}, headers=acme.admin)

        assert response.status_code == 409
        assert 'legal hold' in response.json()['detail']
        assert 'Case 42' in response.json()['detail']
        [shown] = [e for e in await _erasures(client, acme) if e['producer']['id'] == ada]
        assert shown['state'] == 'held'

    async def test_the_list_counts_exports_a_discard_would_end(self, client, acme, gateway):
        ada, incarnation = await _removed(client, acme, gateway)
        asked = await client.post(
            f'/portal/orgs/{acme.org_id}/producers/person/{ada}/exports', json={}, headers=acme.admin
        )

        [shown] = [e for e in await _erasures(client, acme) if e['producer']['id'] == ada]

        assert asked.status_code == 202, asked.text
        assert (shown['state'], shown['exports'], shown['incarnation']) == ('scheduled', 1, incarnation)

    async def test_nobody_removed_is_a_404(self, client, acme):
        response = await client.post(f'/portal/orgs/{acme.org_id}/members/nobody/discard', json={}, headers=acme.admin)

        assert response.status_code == 404

    async def test_only_the_organisations_admins(self, client, acme, gateway):
        ada, _ = await _removed(client, acme, gateway)
        _, member = await acme.person('max@acme.com')

        response = await client.post(f'/portal/orgs/{acme.org_id}/members/{ada}/discard', json={}, headers=member)

        assert response.status_code == 403
