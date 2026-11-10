"""Commands as root in producers' VMs, jobs for many, and the audit log.

Exactly who may: the producer in its own VM (a person with their
session, an app with its token); an org admin in any VM of their
organisation; the super admin anywhere. A command for one VM wakes it; a
job for many is handed to each VM at its next wake, never a mass wake.
The node agent pulls, acknowledges and reports with a platform token
(`docs/service-identity.md` §13).
"""

from __future__ import annotations

import pytest

from tests.cloud_world import CloudOrg, make_super_admin, platform

pytestmark = pytest.mark.anyio


@pytest.fixture
async def acme(client) -> CloudOrg:
    return await CloudOrg(client).create()


async def _run(client, headers, producer: dict, command: str = 'apt-get install -y jq', **extra):
    return await client.post('/vm/commands', json={'producer': producer, 'command': command, **extra}, headers=headers)


async def _pull(client, gateway, org_id: str, kind: str, producer_id: str) -> list[dict]:
    response = await client.post(
        f'/platform/orgs/{org_id}/producers/{kind}/{producer_id}/commands/pull', headers=gateway
    )
    assert response.status_code == 200, response.text
    return list(response.json()['commands'])


class TestWhoMayRunACommand:
    async def test_a_person_in_their_own_vm(self, client, acme):
        ada, headers = await acme.person('ada@acme.com')

        response = await _run(client, headers, {'kind': 'person', 'id': ada})

        assert response.status_code == 202, response.text
        record = response.json()
        assert record['requested_by'] == {'kind': 'person', 'id': ada, 'acting_as': 'producer'}
        assert (record['state'], record['wake']) == ('queued', True)

    async def test_not_in_anybody_elses(self, client, acme):
        _, headers = await acme.person('ada@acme.com')
        max_id, _ = await acme.person('max@acme.com')
        app = await acme.app()

        assert (await _run(client, headers, {'kind': 'person', 'id': max_id})).status_code == 403
        assert (await _run(client, headers, {'kind': 'app', 'id': app.client_id})).status_code == 403

    async def test_an_app_in_its_own_vm_and_no_other(self, client, acme):
        app = await acme.app()
        other = await acme.app('Other bot')
        max_id, _ = await acme.person('max@acme.com')
        headers = await acme.app_token(app)

        own = await _run(client, headers, {'kind': 'app', 'id': app.client_id})

        assert own.status_code == 202
        assert own.json()['requested_by']['acting_as'] == 'producer'
        assert (await _run(client, headers, {'kind': 'app', 'id': other.client_id})).status_code == 403
        assert (await _run(client, headers, {'kind': 'person', 'id': max_id})).status_code == 403

    async def test_an_org_admin_in_any_vm_of_their_organisation_only(self, client, acme):
        ada, _ = await acme.person('ada@acme.com')
        app = await acme.app()
        globex = await CloudOrg(client, slug='globex').create()
        bob, _ = await globex.person('bob@globex.com')

        assert (await _run(client, acme.admin, {'kind': 'person', 'id': ada})).json()['requested_by'][
            'acting_as'
        ] == 'org_admin'
        assert (await _run(client, acme.admin, {'kind': 'app', 'id': app.client_id})).status_code == 202
        assert (await _run(client, acme.admin, {'kind': 'person', 'id': bob}, org_id=globex.org_id)).status_code == 403

    async def test_the_super_admin_anywhere(self, client, acme):
        globex = await CloudOrg(client, slug='globex').create()
        bob, _ = await globex.person('bob@globex.com')
        await make_super_admin(acme.admin_id)

        response = await _run(client, acme.admin, {'kind': 'person', 'id': bob}, org_id=globex.org_id)

        assert response.status_code == 202
        assert response.json()['requested_by']['acting_as'] == 'super_admin'
        assert response.json()['org_id'] == globex.org_id

    async def test_an_unknown_producer_is_404(self, client, acme):
        assert (await _run(client, acme.admin, {'kind': 'person', 'id': 'nobody'})).status_code == 404


class TestTheNodeAgent:
    async def test_pull_ack_result_and_the_audit_log(self, client, acme):
        ada, headers = await acme.person('ada@acme.com')
        gateway = await platform(client)
        asked = (await _run(client, headers, {'kind': 'person', 'id': ada}, command='echo hi')).json()

        wakes = (await client.get('/platform/wakes', headers=gateway)).json()['producers']
        [handed] = await _pull(client, gateway, acme.org_id, 'person', ada)
        again = await _pull(client, gateway, acme.org_id, 'person', ada)
        ack = await client.post(f'/platform/orgs/{acme.org_id}/vm-commands/{handed["command_id"]}/ack', headers=gateway)
        result = await client.post(
            f'/platform/orgs/{acme.org_id}/vm-commands/{handed["command_id"]}/result',
            json={'exit_code': 0, 'output': 'hi\n'},
            headers=gateway,
        )
        twice = await client.post(
            f'/platform/orgs/{acme.org_id}/vm-commands/{handed["command_id"]}/result',
            json={'exit_code': 0},
            headers=gateway,
        )

        assert wakes == [{'org_id': acme.org_id, 'producer': {'kind': 'person', 'id': ada}, 'reasons': ['command']}]
        assert handed['command_id'] == asked['command_id']
        assert handed['command'] == 'echo hi'
        assert again == []
        assert (ack.status_code, result.status_code, twice.status_code) == (204, 204, 409)
        [logged] = (await client.get('/vm/commands', headers=headers)).json()['commands']
        assert (logged['state'], logged['exit_code'], logged['output']) == ('finished', 0, 'hi\n')
        assert logged['started_at'] and logged['finished_at'] and logged['handed_to']
        assert (await client.get('/platform/wakes', headers=gateway)).json()['producers'] == []

    async def test_only_a_platform_client(self, client, acme):
        ada, headers = await acme.person('ada@acme.com')

        response = await client.post(
            f'/platform/orgs/{acme.org_id}/producers/person/{ada}/commands/pull', headers=headers
        )

        assert response.status_code == 401

    async def test_a_queued_command_can_be_cancelled_and_is_not_handed_out(self, client, acme):
        ada, headers = await acme.person('ada@acme.com')
        gateway = await platform(client)
        asked = (await _run(client, headers, {'kind': 'person', 'id': ada})).json()

        cancelled = await client.post(f'/vm/commands/{asked["command_id"]}/cancel', headers=headers)

        assert cancelled.status_code == 204
        assert await _pull(client, gateway, acme.org_id, 'person', ada) == []


class TestJobs:
    async def test_a_group_job_reaches_each_vm_in_it_at_its_next_wake_and_wakes_nobody(self, client, acme):
        legal = await acme.group('Legal')
        ada, _ = await acme.person('ada@acme.com', legal)
        max_id, _ = await acme.person('max@acme.com')
        gateway = await platform(client)

        queued = await client.post(
            '/vm/jobs', json={'scope': {'kind': 'group', 'id': legal}, 'command': 'pip install x'}, headers=acme.admin
        )

        assert queued.status_code == 202, queued.text
        assert (await client.get('/platform/wakes', headers=gateway)).json()['producers'] == []
        [hers] = await _pull(client, gateway, acme.org_id, 'person', ada)
        assert hers['job_id'] == queued.json()['jobs'][0]['job_id']
        assert await _pull(client, gateway, acme.org_id, 'person', max_id) == []
        assert await _pull(client, gateway, acme.org_id, 'person', ada) == []
        [job] = (await client.get('/vm/jobs', headers=acme.admin)).json()['jobs']
        assert (job['handed_out'], job['finished']) == (1, 0)

    async def test_a_group_job_needs_a_group_of_this_organisation(self, client, acme):
        globex = await CloudOrg(client, slug='globex').create()
        theirs = await globex.group('Legal')

        answers = [
            await client.post('/vm/jobs', json={'scope': scope, 'command': 'x'}, headers=acme.admin)
            for scope in ({'kind': 'group'}, {'kind': 'group', 'id': 'no-such-group'}, {'kind': 'group', 'id': theirs})
        ]

        assert [(a.status_code, a.json()['detail']) for a in answers] == [(404, 'Group not found')] * 3
        assert (await client.get('/vm/jobs', headers=acme.admin)).json()['jobs'] == []

    async def test_a_profile_job_and_an_org_job(self, client, acme):
        app = await acme.app()
        ada, _ = await acme.person('ada@acme.com')
        gateway = await platform(client)
        default = (await client.get(f'/portal/orgs/{acme.org_id}/vm-profiles', headers=acme.admin)).json()[
            'default_profile_id'
        ]

        await client.post(
            '/vm/jobs', json={'scope': {'kind': 'profile', 'id': default}, 'command': 'one'}, headers=acme.admin
        )
        await client.post('/vm/jobs', json={'scope': {'kind': 'org'}, 'command': 'two'}, headers=acme.admin)

        assert [c['command'] for c in await _pull(client, gateway, acme.org_id, 'app', app.client_id)] == ['one', 'two']
        assert [c['command'] for c in await _pull(client, gateway, acme.org_id, 'person', ada)] == ['one', 'two']

    async def test_a_cancelled_job_is_handed_to_nobody_new(self, client, acme):
        ada, _ = await acme.person('ada@acme.com')
        gateway = await platform(client)
        job = (
            await client.post('/vm/jobs', json={'scope': {'kind': 'org'}, 'command': 'x'}, headers=acme.admin)
        ).json()['jobs'][0]

        await client.post(f'/vm/jobs/{job["job_id"]}/cancel', headers=acme.admin)

        assert await _pull(client, gateway, acme.org_id, 'person', ada) == []

    async def test_only_admins_queue_jobs_and_only_the_super_admin_platform_wide(self, client, acme):
        _, member = await acme.person('max@acme.com')
        app = await acme.app()
        globex = await CloudOrg(client, slug='globex').create()
        bob, _ = await globex.person('bob@globex.com')
        gateway = await platform(client)
        org_job = {'scope': {'kind': 'org'}, 'command': 'x'}
        platform_job = {'scope': {'kind': 'platform'}, 'command': 'apt-get upgrade -y'}

        assert (await client.post('/vm/jobs', json=org_job, headers=member)).status_code == 403
        assert (await client.post('/vm/jobs', json=org_job, headers=await acme.app_token(app))).status_code == 403
        assert (await client.post('/vm/jobs', json=platform_job, headers=acme.admin)).status_code == 403
        await make_super_admin(acme.admin_id)
        everywhere = await client.post('/vm/jobs', json=platform_job, headers=acme.admin)

        assert everywhere.status_code == 202
        assert {j['org_id'] for j in everywhere.json()['jobs']} >= {acme.org_id, globex.org_id}
        assert len({j['job_id'] for j in everywhere.json()['jobs']}) == 1
        assert [c['command'] for c in await _pull(client, gateway, globex.org_id, 'person', bob)] == [
            'apt-get upgrade -y'
        ]


class TestTheAuditLog:
    async def test_a_member_sees_their_own_and_an_admin_the_organisations(self, client, acme):
        ada, hers = await acme.person('ada@acme.com')
        max_id, his = await acme.person('max@acme.com')
        await _run(client, hers, {'kind': 'person', 'id': ada}, command='mine')
        await _run(client, his, {'kind': 'person', 'id': max_id}, command='his')

        own = (await client.get('/vm/commands', headers=hers)).json()['commands']
        everyone = (await client.get('/vm/commands', headers=acme.admin)).json()['commands']
        filtered = (
            await client.get(f'/vm/commands?producer_kind=person&producer_id={max_id}', headers=acme.admin)
        ).json()['commands']

        assert [c['command'] for c in own] == ['mine']
        assert sorted(c['command'] for c in everyone) == ['his', 'mine']
        assert [c['command'] for c in filtered] == ['his']

    async def test_another_organisations_admin_reads_nothing(self, client, acme):
        globex = await CloudOrg(client, slug='globex').create()

        assert (await client.get(f'/vm/commands?org_id={acme.org_id}', headers=globex.admin)).status_code == 403
