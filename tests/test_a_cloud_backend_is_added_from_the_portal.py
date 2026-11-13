"""A super admin adds a cloud model provider the way they add a GPU node.

The same form, the same rules as the config file — the portal validates
through `InferenceBackend` itself, so the two cannot disagree — and the
same care with secrets: a stored credential goes in, is encrypted at
rest, reaches the router decrypted, and never comes back out.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.api.base_v1.schema.chat import CompletionUsage
from app.db.sql.chat_usage import ChatUsageDB
from app.db.sql.inference_nodes import InferenceNodeDB
from app.db.sql.models.chat_usage import ChatUsage
from app.services.ai.chat.request_context import ChatRequestContext
from app.services.ai.chat.schemas import ChatCaller
from app.services.ai.fair_queue import FairQueue
from app.services.ai.providers.kinds import BackendKind
from app.services.ai.schemas import HealthState, NodeHealth
from tests.chat_proxy import lease, request, SpyPool
from tests.conftest import auth_header, create_org, create_user, make_token

_SERVICE_ACCOUNT = json.dumps({'type': 'service_account', 'private_key': 'PRIVATE-KEY-MATERIAL', 'client_email': 'x'})

VERTEX = {
    'node_id': 'vertex-eu',
    'kind': 'vertex_openai',
    'project': 'acme-ai',
    'region': 'europe-west4',
    'models': ['gemini-pro'],
    'model_ids': {'gemini-pro': 'google/gemini-2.5-pro'},
    'max_concurrent': 200,
}


@pytest.fixture
async def admin(db):
    await create_user(db, user_id='admin-1', email='admin@test.com', is_super_admin=True)
    org = await create_org(db, owner_user_id='admin-1', slug='acme', display_name='Acme')
    return auth_header(make_token('admin-1', org_id=str(org.id))), str(org.id)


@pytest.fixture(autouse=True)
def no_probing(monkeypatch):
    async def _all(self, backends):
        return {b.id: NodeHealth(state=HealthState.HEALTHY) for b in backends}

    monkeypatch.setattr('app.services.ai.health.NodeHealthProbe.check_all', _all)


@pytest.fixture(autouse=True)
def clean_pool(monkeypatch):
    """A provider with no static config, so pinned nodes are all there is."""
    from app.services.ai.backend_provider import InferenceBackendProvider
    from app.services.ai.pool_manager import PoolManager
    from app.utils.singleton import SingletonMeta

    class NoRegistry:
        async def list_nodes(self):
            return []

        async def get(self, node_id):
            return None

    SingletonMeta._instances.pop(PoolManager, None)
    PoolManager(provider=InferenceBackendProvider(path='', inline='', registry=NoRegistry()))
    monkeypatch.setattr('app.services.ai.fleet_status.NodeRegistry', NoRegistry)
    monkeypatch.setattr('app.services.ai.pinned_nodes.NodeRegistry', NoRegistry)
    yield
    SingletonMeta._instances.pop(PoolManager, None)


async def _routed():
    from app.services.ai.pool_manager import PoolManager

    return {backend.id: backend for backend in await PoolManager().provider.load()}


class TestAdding:
    async def test_a_vertex_backend_is_stored_with_the_address_its_project_and_region_make(self, client, admin):
        headers, _org = admin
        resp = await client.post('/portal/inference/nodes', json=VERTEX, headers=headers)

        assert resp.status_code == 201, resp.text
        view = resp.json()
        assert view['kind'] == 'vertex_openai'
        assert view['base_url'].startswith('https://europe-west4-aiplatform.googleapis.com/v1/projects/acme-ai/')
        assert view['model_ids'] == {'gemini-pro': 'google/gemini-2.5-pro'}
        assert view['has_credential'] is False

    async def test_it_is_routed_as_its_kind(self, client, admin):
        headers, _org = admin
        await client.post('/portal/inference/nodes', json=VERTEX, headers=headers)

        backend = (await _routed())['vertex-eu']
        assert (backend.kind, backend.project, backend.region) == (BackendKind.VERTEX_OPENAI, 'acme-ai', 'europe-west4')

    async def test_the_fleet_view_says_which_kind_each_node_is(self, client, admin):
        headers, _org = admin
        await client.post('/portal/inference/nodes', json=VERTEX, headers=headers)

        nodes = (await client.get('/portal/inference', headers=headers)).json()['nodes']
        assert [(n['id'], n['kind']) for n in nodes] == [('vertex-eu', 'vertex_openai')]

    async def test_a_backend_its_kind_cannot_use_is_refused_at_the_door(self, client, admin):
        headers, _org = admin
        resp = await client.post(
            '/portal/inference/nodes', json={**VERTEX, 'api_key': 'vertex-takes-no-key'}, headers=headers
        )
        assert resp.status_code == 422
        assert await InferenceNodeDB().get_by_node_id('vertex-eu') is None

    async def test_a_gpu_node_is_added_exactly_as_before(self, client, admin):
        headers, _org = admin
        node = {'node_id': 'gpu-1', 'base_url': 'http://gpu-1.internal:8000/v1', 'models': ['m']}
        resp = await client.post('/portal/inference/nodes', json=node, headers=headers)
        assert resp.status_code == 201
        assert (resp.json()['kind'], resp.json()['base_url']) == ('openai', 'http://gpu-1.internal:8000/v1')


class TestTheStoredCredential:
    async def test_it_is_encrypted_at_rest_and_reaches_the_router_decrypted(self, client, admin):
        headers, _org = admin
        await client.post('/portal/inference/nodes', json={**VERTEX, 'credential': _SERVICE_ACCOUNT}, headers=headers)

        stored = await InferenceNodeDB().get_by_node_id('vertex-eu')
        assert stored is not None and str(stored.credential).startswith('gAAAAA')
        assert 'PRIVATE-KEY-MATERIAL' not in str(stored.credential)
        assert (await _routed())['vertex-eu'].credential == _SERVICE_ACCOUNT

    async def test_it_never_comes_back(self, client, admin):
        headers, _org = admin
        created = await client.post(
            '/portal/inference/nodes', json={**VERTEX, 'credential': _SERVICE_ACCOUNT}, headers=headers
        )
        read = await client.get('/portal/inference/nodes/vertex-eu', headers=headers)
        fleet = await client.get('/portal/inference', headers=headers)

        for resp in (created, read, fleet):
            assert 'PRIVATE-KEY-MATERIAL' not in resp.text
        assert read.json()['has_credential'] is True

    async def test_an_edit_that_omits_it_leaves_it_and_an_empty_one_clears_it(self, client, admin):
        headers, _org = admin
        await client.post('/portal/inference/nodes', json={**VERTEX, 'credential': _SERVICE_ACCOUNT}, headers=headers)

        await client.patch('/portal/inference/nodes/vertex-eu', json={'max_concurrent': 50}, headers=headers)
        assert (await _routed())['vertex-eu'].credential == _SERVICE_ACCOUNT

        await client.patch('/portal/inference/nodes/vertex-eu', json={'credential': ''}, headers=headers)
        assert (await _routed())['vertex-eu'].credential is None


class TestEditing:
    async def test_moving_a_vertex_backend_moves_its_address(self, client, admin):
        headers, _org = admin
        await client.post('/portal/inference/nodes', json=VERTEX, headers=headers)

        resp = await client.patch('/portal/inference/nodes/vertex-eu', json={'region': 'us-central1'}, headers=headers)

        assert resp.status_code == 200, resp.text
        assert '/locations/us-central1/' in resp.json()['base_url']
        assert (await _routed())['vertex-eu'].base_url.startswith('https://us-central1-aiplatform.googleapis.com/')

    async def test_an_edit_that_would_break_the_backend_is_refused_and_writes_nothing(self, client, admin):
        headers, _org = admin
        await client.post('/portal/inference/nodes', json=VERTEX, headers=headers)

        resp = await client.patch('/portal/inference/nodes/vertex-eu', json={'region': ''}, headers=headers)

        assert resp.status_code == 422
        assert 'project and region' in resp.json()['detail']
        assert (await _routed())['vertex-eu'].region == 'europe-west4'

    async def test_a_model_map_naming_a_model_not_served_is_refused(self, client, admin):
        headers, _org = admin
        await client.post('/portal/inference/nodes', json=VERTEX, headers=headers)

        resp = await client.patch(
            '/portal/inference/nodes/vertex-eu', json={'model_ids': {'unknown': 'x'}}, headers=headers
        )

        assert resp.status_code == 422


class TestUsageNamesTheBackend:
    async def test_a_usage_row_records_the_backend_and_its_kind(self, client, admin):
        _headers, org = admin
        pool = SpyPool()
        ctx = ChatRequestContext(
            caller=ChatCaller(user_id='admin-1', org_id=org),
            request=request(),
            lease=lease(1).model_copy(update={'backend_id': 'vertex-eu', 'kind': BackendKind.VERTEX_OPENAI}),
            pool=pool,
            usage_db=ChatUsageDB(),
            queue=FairQueue(max_length=10),
        )

        await ctx.log_usage(
            CompletionUsage(prompt_tokens=3, completion_tokens=4, total_tokens=7), duration=1.0, stream=False
        )

        async with ChatUsageDB().session_scope(org) as session:
            (row,) = (await session.execute(select(ChatUsage))).scalars().all()
        assert (row.backend_id, row.kind, row.total_tokens) == ('vertex-eu', 'vertex_openai', 7)
