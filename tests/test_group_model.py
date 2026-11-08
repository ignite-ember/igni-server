"""A group pinned to its own model.

The case: a legal team should reach a LoRA adapter trained for legal
review, engineering a different one, and nobody should have to remember
to type the adapter's name. Since a node serves its base model and every
adapter on the same GPU, this is a routing choice rather than a hardware
one — which is why it lives on the group and is resolved server-side.

The rules that matter, in order of how much damage getting them wrong
would do:

* An explicit request always wins. A group default that overrode what
  somebody actually asked for would be silent misrouting.
* A group with no default changes nothing.
* A model no node serves is refused when it is *set*, not when it is
  used. Otherwise the typo lands on the group's users rather than on the
  admin who made it.
* Nothing here may break chat. A group lookup that fails should cost the
  default, not the request.
"""

import pytest

from app.db.sql.schemas.tenancy import OrganisationDetached
from app.services.portal.group_model_service import GroupModelService
from tests.conftest import add_member, auth_header, create_org, create_user, make_token

MODELS = [('igni-1.0-27b', 'http://gpu-01:8000/v1'), ('legal-reviewer', 'http://gpu-01:8000/v1')]


@pytest.fixture(autouse=True)
def served_models(monkeypatch):
    """A pool serving a base model and one adapter."""

    async def _list(self):
        return MODELS

    monkeypatch.setattr('app.services.ai.backend_pool.InferenceBackendPool.list_available_models', _list)


@pytest.fixture
async def admin(db):
    await create_user(db, user_id='admin-1', email='admin@test.com')
    org = await create_org(db, owner_user_id='admin-1', slug='acme', display_name='Acme')
    return auth_header(make_token('admin-1', org_id=str(org.id))), str(org.id)


class TestSettingIt:
    async def test_a_group_can_be_created_with_a_model(self, client, admin):
        headers, _ = admin
        resp = await client.post(
            '/portal/groups',
            json={'name': 'Legal', 'default_model': 'legal-reviewer'},
            headers=headers,
        )
        assert resp.status_code in (200, 201)
        assert resp.json()['default_model'] == 'legal-reviewer'

    async def test_it_can_be_set_afterwards(self, client, admin):
        headers, _ = admin
        group = (await client.post('/portal/groups', json={'name': 'Legal'}, headers=headers)).json()

        resp = await client.patch(
            f'/portal/groups/{group["id"]}',
            json={'default_model': 'legal-reviewer'},
            headers=headers,
        )
        assert resp.json()['default_model'] == 'legal-reviewer'

    async def test_an_empty_string_clears_it(self, client, admin):
        """``None`` already means "not supplied" on a partial update, so
        clearing needs its own spelling."""
        headers, _ = admin
        group = (
            await client.post(
                '/portal/groups', json={'name': 'Legal', 'default_model': 'legal-reviewer'}, headers=headers
            )
        ).json()

        resp = await client.patch(f'/portal/groups/{group["id"]}', json={'default_model': ''}, headers=headers)
        assert resp.json()['default_model'] is None

    async def test_an_unrelated_edit_leaves_it_alone(self, client, admin):
        headers, _ = admin
        group = (
            await client.post(
                '/portal/groups', json={'name': 'Legal', 'default_model': 'legal-reviewer'}, headers=headers
            )
        ).json()

        resp = await client.patch(f'/portal/groups/{group["id"]}', json={'description': 'x'}, headers=headers)
        assert resp.json()['default_model'] == 'legal-reviewer'


class TestRefusingWhatNothingServes:
    async def test_a_model_no_node_serves_is_refused(self, client, admin):
        """At the moment it is set, so the typo lands on the admin who
        made it rather than on everyone in the group."""
        headers, _ = admin
        resp = await client.post(
            '/portal/groups', json={'name': 'Legal', 'default_model': 'legal-reveiwer'}, headers=headers
        )
        assert resp.status_code == 409
        assert 'legal-reveiwer' in resp.json()['detail']

    async def test_the_refusal_lists_what_is_available(self, client, admin):
        """Adapter names are whatever somebody passed to --lora-modules,
        so the fix is usually visible in the list."""
        headers, _ = admin
        resp = await client.post('/portal/groups', json={'name': 'Legal', 'default_model': 'nope'}, headers=headers)
        assert 'legal-reviewer' in resp.json()['detail']

    async def test_a_broken_pool_does_not_block_group_edits(self, client, admin, monkeypatch):
        """Group administration should not require the GPUs to be up."""

        async def _boom(self):
            raise ConnectionError('redis is down')

        monkeypatch.setattr('app.services.ai.backend_pool.InferenceBackendPool.list_available_models', _boom)
        headers, _ = admin
        resp = await client.post(
            '/portal/groups', json={'name': 'Legal', 'default_model': 'legal-reviewer'}, headers=headers
        )
        assert resp.status_code in (200, 201)

    async def test_a_pool_that_cannot_be_read_does_not_block_the_edit(self, client, admin, monkeypatch):
        """However the pool fails to answer — not only a socket or Redis —
        the served check is skipped and the group is still saved. Group
        administration must not require the GPUs to be up."""

        async def _broken(self):
            raise RuntimeError('the node registry returned something unreadable')

        monkeypatch.setattr('app.services.ai.backend_pool.InferenceBackendPool.list_available_models', _broken)
        headers, _ = admin

        resp = await client.post(
            '/portal/groups', json={'name': 'Legal', 'default_model': 'legal-reviewer'}, headers=headers
        )

        assert resp.status_code == 201, resp.text
        assert resp.json()['default_model'] == 'legal-reviewer'


class TestRouting:
    async def test_a_member_gets_their_group_model(self, client, admin, db):
        headers, org_id = admin
        await create_user(db, user_id='lawyer-1', email='lawyer@test.com')
        await add_member(db, org_id=org_id, user_id='lawyer-1', role='member')

        group = (
            await client.post(
                '/portal/groups', json={'name': 'Legal', 'default_model': 'legal-reviewer'}, headers=headers
            )
        ).json()
        await client.post(f'/portal/groups/{group["id"]}/members', json={'user_id': 'lawyer-1'}, headers=headers)

        assert await GroupModelService().model_for_user('lawyer-1') == 'legal-reviewer'

    async def test_someone_in_no_group_gets_nothing(self, client, admin, db):
        """Which leaves the deployment default — whatever the chosen node
        advertises first."""
        await create_user(db, user_id='nomad-1', email='nomad@test.com')
        assert await GroupModelService().model_for_user('nomad-1') is None

    async def test_a_group_with_no_model_changes_nothing(self, client, admin, db):
        headers, org_id = admin
        await create_user(db, user_id='dev-1', email='dev@test.com')
        await add_member(db, org_id=org_id, user_id='dev-1', role='member')

        group = (await client.post('/portal/groups', json={'name': 'Eng'}, headers=headers)).json()
        await client.post(f'/portal/groups/{group["id"]}/members', json={'user_id': 'dev-1'}, headers=headers)

        assert await GroupModelService().model_for_user('dev-1') is None

    async def test_a_failed_lookup_costs_the_default_not_the_request(self, monkeypatch):
        """Consulted on every chat request that names no model, so it must
        never be the thing that fails one."""

        async def _boom(self, *args, **kwargs):
            raise ConnectionError('database is down')

        # The *active* variant is what `model_for_user` calls (B8);
        # patching the other left this passing without exercising anything.
        monkeypatch.setattr('app.db.sql.group_summaries.GroupSummariesDB.get_active_user_group_summary', _boom)
        assert await GroupModelService().model_for_user('anyone') is None

    async def test_any_failed_lookup_costs_the_default_not_the_request(self, monkeypatch):
        """Not only a database that is down: an organisation detached
        between the membership read and the group read fails the lookup
        with an error of its own, and chat still goes ahead."""

        async def _detached(self, *args, **kwargs):
            raise OrganisationDetached('org-gone')

        monkeypatch.setattr('app.db.sql.group_summaries.GroupSummariesDB.get_active_user_group_summary', _detached)
        assert await GroupModelService().model_for_user('anyone') is None


class TestPerAgentModel:
    """One group, several adapters.

    The group default answers "what does this team use". It cannot answer
    "what does *this agent* use" — and a legal team plausibly wants
    contract review on one adapter and case summaries on another. So an
    agent may name its own, and falls back to the group's when it does
    not.
    """

    @staticmethod
    def _entry(resp, name='reviewer'):
        """These endpoints answer with the group's whole entry list
        rather than the single row, so pick the one we asked about."""
        return next(o for o in resp.json()['entries'] if o['entry_name'] == name)

    @staticmethod
    async def _agent(client, headers, group_id, name='reviewer', **extra):
        return await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'agents',
                'entry_name': name,
                'content': f'# {name}',
                'content_type': 'markdown',
                **extra,
            },
            headers=headers,
        )

    @staticmethod
    async def _group(client, headers, **extra):
        resp = await client.post('/portal/groups', json={'name': 'Legal', **extra}, headers=headers)
        return resp.json()['id']

    async def test_an_agent_can_name_its_own_model(self, client, admin):
        headers, _ = admin
        group_id = await self._group(client, headers)

        resp = await self._agent(client, headers, group_id, model='legal-reviewer')
        assert resp.status_code in (200, 201)
        assert self._entry(resp)['model'] == 'legal-reviewer'

    async def test_an_agent_that_names_none_stays_null(self, client, admin):
        """Which is how it inherits the group's, then the deployment's."""
        headers, _ = admin
        group_id = await self._group(client, headers)

        resp = await self._agent(client, headers, group_id)
        assert self._entry(resp)['model'] is None

    async def test_it_can_be_set_afterwards(self, client, admin):
        headers, _ = admin
        group_id = await self._group(client, headers)
        entry = self._entry(await self._agent(client, headers, group_id))

        resp = await client.patch(
            f'/portal/groups/{group_id}/entries/{entry["id"]}',
            json={'model': 'legal-reviewer'},
            headers=headers,
        )
        assert self._entry(resp)['model'] == 'legal-reviewer'

    async def test_an_empty_string_clears_it(self, client, admin):
        headers, _ = admin
        group_id = await self._group(client, headers)
        entry = self._entry(await self._agent(client, headers, group_id, model='legal-reviewer'))

        resp = await client.patch(
            f'/portal/groups/{group_id}/entries/{entry["id"]}',
            json={'model': ''},
            headers=headers,
        )
        assert self._entry(resp)['model'] is None

    async def test_an_unrelated_edit_leaves_it_alone(self, client, admin):
        headers, _ = admin
        group_id = await self._group(client, headers)
        entry = self._entry(await self._agent(client, headers, group_id, model='legal-reviewer'))

        resp = await client.patch(
            f'/portal/groups/{group_id}/entries/{entry["id"]}',
            json={'enabled': False},
            headers=headers,
        )
        assert self._entry(resp)['model'] == 'legal-reviewer'

    async def test_a_model_no_node_serves_is_refused(self, client, admin):
        """Same reasoning as the group default: the typo should land on
        the admin who made it, not on the agent's users."""
        headers, _ = admin
        group_id = await self._group(client, headers)

        resp = await self._agent(client, headers, group_id, model='legal-reveiwer')
        assert resp.status_code == 409
        assert 'legal-reviewer' in resp.json()['detail']

    async def test_it_is_refused_on_an_edit_too(self, client, admin):
        headers, _ = admin
        group_id = await self._group(client, headers)
        entry = self._entry(await self._agent(client, headers, group_id))

        resp = await client.patch(
            f'/portal/groups/{group_id}/entries/{entry["id"]}',
            json={'model': 'legal-reveiwer'},
            headers=headers,
        )
        assert resp.status_code == 409

    async def test_only_agents_may_name_one(self, client, admin):
        """An MCP server or a settings file has no model to run against,
        so accepting one would store something nothing ever reads."""
        headers, _ = admin
        group_id = await self._group(client, headers)

        resp = await client.post(
            f'/portal/groups/{group_id}/entries',
            json={
                'kind': 'mcps',
                'entry_name': 'filesystem',
                'content': '{}',
                'content_type': 'json',
                'model': 'legal-reviewer',
            },
            headers=headers,
        )
        assert resp.status_code == 422
        assert 'mcps' in resp.json()['detail']

    async def test_the_pack_carries_it(self, client, admin, db):
        """The client resolves per-agent routing, so the pack has to say
        both the agent's model and the group's fallback."""
        headers, org_id = admin
        await add_member(db, org_id=org_id, user_id='admin-1', role='admin')
        group_id = await self._group(client, headers, default_model='igni-1.0-27b')
        await client.post(f'/portal/groups/{group_id}/members', json={'user_id': 'admin-1'}, headers=headers)
        await self._agent(client, headers, group_id, name='contracts', model='legal-reviewer')
        await self._agent(client, headers, group_id, name='general')

        pack = (await client.get('/portal/me/group/pack', headers=headers)).json()
        by_name = {o['entry_name']: o for o in pack['entries']}
        assert by_name['contracts']['model'] == 'legal-reviewer'
        assert by_name['general']['model'] is None
        assert pack['default_model'] == 'igni-1.0-27b'
