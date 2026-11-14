"""A super admin adds a cloud model provider the way they add a GPU node.

The same form and the same rules as the config file — the portal validates
through `InferenceBackend` itself, so the two cannot disagree — and the
same care with secrets: a stored credential goes in, is encrypted at rest,
reaches the router decrypted, and never comes back out.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.api.base_v1.schema.chat import CompletionUsage
from app.db.sql.chat_usage import ChatUsageDB
from app.db.sql.inference_nodes import InferenceNodeDB
from app.db.sql.models.chat_usage import ChatUsage
from app.services.ai.chat.schemas import ChatCaller
from app.services.ai.providers.credentials import CloudCredentials
from app.services.ai.providers.kinds import BackendKind
from tests.chat_proxy import context, lease, SpyPool
from tests.cloud_fakes import SERVICE_ACCOUNT
from tests.portal_backends import routed, VERTEX

pytestmark = pytest.mark.usefixtures('no_probing', 'only_pinned_nodes')

_VERTEX_ADDRESS = (
    'https://europe-west4-aiplatform.googleapis.com/v1/projects/acme-ai/locations/europe-west4/endpoints/openapi'
)


class TestAdding:
    async def test_a_vertex_backend_is_stored_with_the_address_its_project_and_region_make(self, client, fleet_admin):
        headers, _org = fleet_admin
        resp = await client.post('/portal/inference/nodes', json=VERTEX, headers=headers)

        assert resp.status_code == 201, resp.text
        view = resp.json()
        assert (view['kind'], view['base_url'], view['model_ids'], view['has_credential'], view['auth']) == (
            'vertex_openai',
            _VERTEX_ADDRESS,
            {'gemini-pro': 'google/gemini-2.5-pro'},
            False,
            'ambient',
        )

    async def test_it_is_routed_as_its_kind(self, client, fleet_admin):
        headers, _org = fleet_admin
        await client.post('/portal/inference/nodes', json=VERTEX, headers=headers)

        backend = (await routed())['vertex-eu']
        assert (backend.kind, backend.project, backend.region, backend.base_url) == (
            BackendKind.VERTEX_OPENAI,
            'acme-ai',
            'europe-west4',
            _VERTEX_ADDRESS,
        )

    async def test_the_fleet_view_says_which_kind_each_node_is(self, client, fleet_admin):
        headers, _org = fleet_admin
        await client.post('/portal/inference/nodes', json=VERTEX, headers=headers)

        nodes = (await client.get('/portal/inference', headers=headers)).json()['nodes']
        assert [(n['id'], n['kind']) for n in nodes] == [('vertex-eu', 'vertex_openai')]

    async def test_the_fleet_view_asks_the_apps_credentials_and_builds_nothing_else(self, app, client, fleet_admin):
        # Building the chat services here would pin this test's pool to the app for every later one.
        headers, _org = fleet_admin
        before = dict(app.state._state)
        app.state._state.pop('chat_services', None)
        app.state._state.pop('cloud_credentials', None)
        try:
            await client.get('/portal/inference', headers=headers)

            assert 'chat_services' not in app.state._state
            assert app.state.cloud_credentials is CloudCredentials.of_app(app)
        finally:
            app.state._state.clear()
            app.state._state.update(before)

    async def test_a_backend_its_kind_cannot_use_is_refused_saying_why_and_nothing_is_stored(self, client, fleet_admin):
        headers, _org = fleet_admin
        resp = await client.post(
            '/portal/inference/nodes', json={**VERTEX, 'api_key': 'vertex-takes-no-key'}, headers=headers
        )

        assert resp.status_code == 422
        assert resp.json()['detail'] == (
            "Node 'vertex-eu' was not added: Backend 'vertex-eu' (vertex_openai) with ambient auth: takes no api_key"
        )
        assert await InferenceNodeDB().get_by_node_id('vertex-eu') is None

    async def test_a_gpu_node_is_added_exactly_as_before(self, client, fleet_admin):
        headers, _org = fleet_admin
        node = {'node_id': 'gpu-1', 'base_url': 'http://gpu-1.internal:8000/v1', 'models': ['m']}
        resp = await client.post('/portal/inference/nodes', json=node, headers=headers)
        assert resp.status_code == 201
        assert (resp.json()['kind'], resp.json()['base_url'], resp.json()['auth']) == (
            'openai',
            'http://gpu-1.internal:8000/v1',
            'none',
        )


class TestTheStoredCredential:
    async def test_it_is_encrypted_at_rest_and_reaches_the_router_decrypted(self, client, fleet_admin):
        headers, _org = fleet_admin
        await client.post('/portal/inference/nodes', json={**VERTEX, 'credential': SERVICE_ACCOUNT}, headers=headers)

        stored = await InferenceNodeDB().get_by_node_id('vertex-eu')
        assert stored is not None and str(stored.credential).startswith('gAAAAA')
        assert 'PRIVATE-KEY-MATERIAL' not in str(stored.credential)
        assert (await routed())['vertex-eu'].credential == SERVICE_ACCOUNT

    async def test_it_never_comes_back(self, client, fleet_admin):
        headers, _org = fleet_admin
        created = await client.post(
            '/portal/inference/nodes', json={**VERTEX, 'credential': SERVICE_ACCOUNT}, headers=headers
        )
        read = await client.get('/portal/inference/nodes/vertex-eu', headers=headers)
        fleet = await client.get('/portal/inference', headers=headers)

        for resp in (created, read, fleet):
            assert 'PRIVATE-KEY-MATERIAL' not in resp.text
        assert (read.json()['has_credential'], read.json()['auth']) == (True, 'service_account_key')

    async def test_an_edit_that_omits_it_leaves_it_and_an_empty_one_clears_it(self, client, fleet_admin):
        headers, _org = fleet_admin
        await client.post('/portal/inference/nodes', json={**VERTEX, 'credential': SERVICE_ACCOUNT}, headers=headers)

        await client.patch('/portal/inference/nodes/vertex-eu', json={'max_concurrent': 50}, headers=headers)
        assert (await routed())['vertex-eu'].credential == SERVICE_ACCOUNT

        await client.patch('/portal/inference/nodes/vertex-eu', json={'credential': ''}, headers=headers)
        after = (await routed())['vertex-eu']
        assert (after.credential, after.auth.value) == (None, 'ambient')


class TestEditing:
    async def test_moving_a_vertex_backend_moves_its_address(self, client, fleet_admin):
        headers, _org = fleet_admin
        await client.post('/portal/inference/nodes', json=VERTEX, headers=headers)

        resp = await client.patch('/portal/inference/nodes/vertex-eu', json={'region': 'us-central1'}, headers=headers)

        moved = (
            'https://us-central1-aiplatform.googleapis.com/v1/projects/acme-ai/locations/us-central1/endpoints/openapi'
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()['base_url'] == moved
        assert (await routed())['vertex-eu'].base_url == moved

    async def test_an_edit_that_would_break_the_backend_is_refused_and_writes_nothing(self, client, fleet_admin):
        headers, _org = fleet_admin
        await client.post('/portal/inference/nodes', json=VERTEX, headers=headers)

        resp = await client.patch('/portal/inference/nodes/vertex-eu', json={'region': ''}, headers=headers)

        assert resp.status_code == 422
        assert resp.json()['detail'] == (
            "Node 'vertex-eu' was not changed: Backend 'vertex-eu' (vertex_openai): "
            'a Vertex backend needs its project and region'
        )
        assert (await routed())['vertex-eu'].region == 'europe-west4'

    async def test_a_model_map_naming_a_model_not_served_is_refused_and_writes_nothing(self, client, fleet_admin):
        headers, _org = fleet_admin
        await client.post('/portal/inference/nodes', json=VERTEX, headers=headers)

        resp = await client.patch(
            '/portal/inference/nodes/vertex-eu', json={'model_ids': {'unknown': 'x'}}, headers=headers
        )

        assert resp.status_code == 422
        assert resp.json()['detail'].endswith('model_ids names models it does not serve: unknown')
        assert (await routed())['vertex-eu'].model_ids == {'gemini-pro': 'google/gemini-2.5-pro'}


class TestUsageNamesTheBackend:
    async def test_a_usage_row_records_the_backend_and_its_kind(self, fleet_admin):
        _headers, org = fleet_admin
        ctx = context(
            SpyPool(),
            usage_db=ChatUsageDB(),
            caller=ChatCaller(user_id='admin-1', org_id=org),
            held=lease(1).model_copy(update={'backend_id': 'vertex-eu', 'kind': BackendKind.VERTEX_OPENAI}),
        )

        await ctx.log_usage(
            CompletionUsage(prompt_tokens=3, completion_tokens=4, total_tokens=7), duration=1.0, stream=False
        )

        async with ChatUsageDB().session_scope(org) as session:
            (row,) = (await session.execute(select(ChatUsage))).scalars().all()
        assert (row.backend_id, row.kind, row.total_tokens) == ('vertex-eu', 'vertex_openai', 7)
