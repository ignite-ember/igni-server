"""The registration API, and the portal view over it.

Two audiences, two sets of risks.

The **registration endpoints** are called by cluster automation holding a
shared secret that lives in Terraform and cloud-init. That is a wide
blast radius, so the tests here are mostly about what an unauthenticated
or wrongly-authenticated caller cannot do — and the answer has to be
"anything at all", because the thing it would be doing is adding a
machine that every developer's prompts get sent to.

The **portal endpoint** exposes the cluster's internal topology. Base URLs
and node names describe a private network, so it is admin-only, and it
must never carry an ``api_key``.
"""

import json

import pytest

from app.services.ai.schemas import InferenceBackend, RegisteredNode
from app.utils.singleton import SingletonMeta
from tests.conftest import add_member, auth_header, create_org, create_user, make_token

NODE_TOKEN = 'node-token-for-tests'
HEADERS = {'X-Igni-Node-Token': NODE_TOKEN}

REGISTRATION = {
    'id': 'gpu-burst-01',
    'base_url': 'http://gpu-burst-01.internal:8000/v1',
    'model': 'igni-1.0-27b',
    'max_concurrent': 64,
}


class FakeRegistry:
    """An in-memory stand-in shared by every NodeRegistry() in a test."""

    nodes: dict[str, RegisteredNode] = {}

    async def register(self, backend: InferenceBackend) -> RegisteredNode:
        node = RegisteredNode(backend=backend, registered_at=100.0, last_seen=100.0)
        FakeRegistry.nodes[backend.id] = node
        return node

    async def heartbeat(self, node_id: str) -> RegisteredNode | None:
        node = FakeRegistry.nodes.get(node_id)
        if node is None:
            return None
        refreshed = node.model_copy(update={'last_seen': node.last_seen + 1})
        FakeRegistry.nodes[node_id] = refreshed
        return refreshed

    async def deregister(self, node_id: str) -> bool:
        return FakeRegistry.nodes.pop(node_id, None) is not None

    async def get(self, node_id: str) -> RegisteredNode | None:
        return FakeRegistry.nodes.get(node_id)

    async def list_nodes(self) -> list[RegisteredNode]:
        return sorted(FakeRegistry.nodes.values(), key=lambda n: n.backend.id)


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    """Swap the registry everywhere it is constructed.

    The endpoints build their own ``NodeRegistry()`` per request rather
    than taking one as a dependency, so the substitution is at the class.
    """
    FakeRegistry.nodes = {}
    monkeypatch.setattr('app.services.ai.node_registry.NodeRegistry', FakeRegistry)
    monkeypatch.setattr('app.api.base_v1.endpoints.client.nodes.NodeRegistry', FakeRegistry)
    monkeypatch.setattr('app.services.ai.fleet_status.NodeRegistry', FakeRegistry)
    monkeypatch.setattr('app.services.ai.pinned_nodes.NodeRegistry', FakeRegistry)
    return FakeRegistry


@pytest.fixture
async def admin_headers(db):
    # Changing the shared fleet is a super admin's (SER-32).
    await create_user(db, user_id='admin-1', email='admin@test.com', is_super_admin=True)
    org = await create_org(db, owner_user_id='admin-1', slug='acme', display_name='Acme')
    return auth_header(make_token('admin-1', org_id=str(org.id)))


@pytest.fixture
async def member_headers(db):
    await create_user(db, user_id='admin-1', email='admin@test.com')
    await create_user(db, user_id='dev-1', email='dev@test.com')
    org = await create_org(db, owner_user_id='admin-1', slug='acme', display_name='Acme')
    await add_member(db, org_id=str(org.id), user_id='dev-1', role='member')
    return auth_header(make_token('dev-1', org_id=str(org.id)))


@pytest.fixture
def registration_enabled(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, 'node_registration_token', NODE_TOKEN)


@pytest.fixture(autouse=True)
def static_backends(tmp_path):
    """One statically-configured node, so the collision case is real."""
    from app.services.ai.backend_provider import InferenceBackendProvider
    from app.services.ai.pool_manager import PoolManager

    path = tmp_path / 'backends.json'
    path.write_text(
        json.dumps(
            [
                {
                    'id': 'gpu-fixed-01',
                    'base_url': 'http://gpu-fixed-01.internal:8000/v1',
                    'model': 'igni-1.0-27b',
                    'max_concurrent': 32,
                }
            ]
        )
    )
    # PoolManager is a singleton, so the instance has to be evicted or a
    # provider built by an earlier test leaks into this one.
    SingletonMeta._instances.pop(PoolManager, None)
    PoolManager(provider=InferenceBackendProvider(path=str(path), registry=FakeRegistry()))
    yield
    SingletonMeta._instances.pop(PoolManager, None)


class TestRegistrationIsGated:
    async def test_it_does_not_exist_when_no_token_is_configured(self, client):
        """A deployment describing its GPUs statically should not be
        running an endpoint that accepts new inference targets. 404 rather
        than 503, because there is nothing there to be unavailable."""
        resp = await client.post('/nodes', json=REGISTRATION)
        assert resp.status_code == 404

    async def test_a_missing_token_is_rejected(self, client, registration_enabled):
        resp = await client.post('/nodes', json=REGISTRATION)
        assert resp.status_code == 401

    async def test_a_wrong_token_is_rejected(self, client, registration_enabled):
        """The thing an attacker gets by guessing is a node every
        developer's prompts are sent to."""
        resp = await client.post('/nodes', json=REGISTRATION, headers={'X-Igni-Node-Token': 'wrong'})
        assert resp.status_code == 401

    async def test_an_unauthenticated_call_registers_nothing(self, client, registration_enabled, registry):
        resp = await client.post('/nodes', json=REGISTRATION, headers={'X-Igni-Node-Token': 'wrong'})
        assert resp.status_code == 401
        assert registry.nodes == {}

    async def test_every_verb_is_guarded(self, client, registration_enabled):
        """Attached to the subtree rather than per route, so a fourth verb
        cannot be added unauthenticated by omission."""
        assert (await client.post('/nodes/gpu-1/heartbeat')).status_code == 401
        assert (await client.delete('/nodes/gpu-1')).status_code == 401


class TestRegistering:
    async def test_a_node_registers(self, client, registration_enabled, registry):
        resp = await client.post('/nodes', json=REGISTRATION, headers=HEADERS)
        assert resp.status_code == 201
        assert 'gpu-burst-01' in registry.nodes

    async def test_the_response_says_how_often_to_check_in(self, client, registration_enabled):
        """Read from the server rather than hardcoded in the customer's
        cron, so a later release can change it without silently breaking
        every deployment's heartbeat loop."""
        resp = await client.post('/nodes', json=REGISTRATION, headers=HEADERS)
        assert resp.json()['heartbeat_interval_seconds'] > 0

    async def test_registering_twice_is_not_an_error(self, client, registration_enabled):
        """A node that restarts and re-announces is the normal case."""
        assert (await client.post('/nodes', json=REGISTRATION, headers=HEADERS)).status_code == 201
        assert (await client.post('/nodes', json=REGISTRATION, headers=HEADERS)).status_code == 201

    async def test_a_statically_configured_id_is_refused(self, client, registration_enabled, registry):
        """Rejected here, where the automation that got the id wrong is
        watching, rather than silently shadowing the operator's config and
        being found later as a routing mystery."""
        resp = await client.post(
            '/nodes',
            json={**REGISTRATION, 'id': 'gpu-fixed-01'},
            headers=HEADERS,
        )
        assert resp.status_code == 409
        assert 'static' in resp.json()['detail']
        assert registry.nodes == {}

    async def test_a_relative_base_url_is_refused(self, client, registration_enabled):
        """It would be accepted, stored, and then fail on every request
        routed to it."""
        resp = await client.post('/nodes', json={**REGISTRATION, 'base_url': 'gpu-1:8000'}, headers=HEADERS)
        assert resp.status_code == 422

    async def test_an_id_with_a_colon_is_refused(self, client, registration_enabled):
        """Ids namespace Redis keys, so a colon would let a caller reach
        outside its own."""
        resp = await client.post('/nodes', json={**REGISTRATION, 'id': 'gpu:evil'}, headers=HEADERS)
        assert resp.status_code == 422

    async def test_zero_capacity_is_refused(self, client, registration_enabled):
        """A node that can serve nothing is never least-loaded, so it
        would sit in the list looking healthy and take no traffic."""
        resp = await client.post('/nodes', json={**REGISTRATION, 'max_concurrent': 0}, headers=HEADERS)
        assert resp.status_code == 422


class TestHeartbeatAndStop:
    async def test_a_heartbeat_keeps_a_node_alive(self, client, registration_enabled):
        await client.post('/nodes', json=REGISTRATION, headers=HEADERS)
        resp = await client.post('/nodes/gpu-burst-01/heartbeat', headers=HEADERS)
        assert resp.status_code == 200

    async def test_a_heartbeat_for_an_unknown_node_says_register_again(self, client, registration_enabled):
        """The node's correct response is to re-register with its full
        description, and a 404 is how it is told to."""
        resp = await client.post('/nodes/never-registered/heartbeat', headers=HEADERS)
        assert resp.status_code == 404
        assert 'register it again' in resp.json()['detail']

    async def test_stopping_a_cluster_removes_its_node(self, client, registration_enabled, registry):
        await client.post('/nodes', json=REGISTRATION, headers=HEADERS)
        resp = await client.delete('/nodes/gpu-burst-01', headers=HEADERS)
        assert resp.status_code == 204
        assert registry.nodes == {}

    async def test_deleting_twice_still_succeeds(self, client, registration_enabled):
        """A stop-cluster job retrying after a timeout must not fail the
        second time."""
        await client.post('/nodes', json=REGISTRATION, headers=HEADERS)
        assert (await client.delete('/nodes/gpu-burst-01', headers=HEADERS)).status_code == 204
        assert (await client.delete('/nodes/gpu-burst-01', headers=HEADERS)).status_code == 204


class TestPortalView:
    async def test_it_is_admin_only(self, client, member_headers):
        """The node list describes the shape of a private network, which
        an ordinary developer has no reason to enumerate."""
        resp = await client.get('/portal/inference', headers=member_headers)
        assert resp.status_code == 403

    async def test_it_lists_static_and_registered_nodes_together(self, client, admin_headers, registry):
        await registry().register(
            InferenceBackend(
                id='gpu-burst-01',
                base_url='http://gpu-burst-01.internal:8000/v1',
                model='igni-1.0-27b',
            )
        )
        resp = await client.get('/portal/inference', headers=admin_headers)
        assert resp.status_code == 200

        by_id = {node['id']: node for node in resp.json()['nodes']}
        assert by_id['gpu-fixed-01']['source'] == 'static'
        assert by_id['gpu-burst-01']['source'] == 'registered'

    async def test_a_registered_node_reports_when_it_last_checked_in(self, client, admin_headers, registry):
        """ "Registered, last heartbeat 40s ago" is the difference between
        a node an operator trusts and one they go and look at."""
        await registry().register(InferenceBackend(id='gpu-burst-01', base_url='http://b:8000/v1', model='m'))
        resp = await client.get('/portal/inference', headers=admin_headers)
        node = next(n for n in resp.json()['nodes'] if n['id'] == 'gpu-burst-01')
        assert node['last_seen_seconds_ago'] is not None

    async def test_a_static_node_has_no_heartbeat_to_be_late_on(self, client, admin_headers):
        resp = await client.get('/portal/inference', headers=admin_headers)
        node = next(n for n in resp.json()['nodes'] if n['id'] == 'gpu-fixed-01')
        assert node['last_seen_seconds_ago'] is None

    async def test_it_never_exposes_an_api_key(self, client, admin_headers, registry):
        """The portal view and the routing model are separate types for
        exactly this reason — one ``response_model`` slip otherwise
        publishes the customer's proxy credentials."""
        await registry().register(
            InferenceBackend(
                id='gpu-burst-01',
                base_url='http://b:8000/v1',
                model='m',
                api_key='super-secret',
            )
        )
        resp = await client.get('/portal/inference', headers=admin_headers)
        assert 'super-secret' not in resp.text
        assert 'api_key' not in resp.text

    async def test_it_reports_capacity_and_utilization(self, client, admin_headers):
        """The numbers that answer "do we need another GPU"."""
        resp = await client.get('/portal/inference', headers=admin_headers)
        status = resp.json()
        assert status['total_capacity'] == 32
        assert status['utilization'] == 0.0
        assert [n['id'] for n in status['nodes']] == ['gpu-fixed-01']

    async def test_it_says_whether_registration_is_available(self, client, admin_headers):
        """ "Nothing has registered" and "this deployment does not accept
        registrations" look identical otherwise, and they call for
        completely different fixes."""
        resp = await client.get('/portal/inference', headers=admin_headers)
        assert resp.json()['registration_enabled'] is False

    async def test_it_says_so_when_registration_is_on(self, client, admin_headers, registration_enabled):
        resp = await client.get('/portal/inference', headers=admin_headers)
        assert resp.json()['registration_enabled'] is True
