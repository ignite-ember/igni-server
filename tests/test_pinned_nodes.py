"""Adding a GPU node from the portal.

The third source of backends, and the one a person operates. It exists
because the other two both need somebody outside the product: the static
file needs a redeploy, and registration needs cluster automation holding
a token. Neither helps an admin who has just been handed a machine's
address.

Two properties carry the design:

* **It never expires.** Registered nodes live on a heartbeat TTL because
  a node that stops checking in has gone away. Nothing heartbeats for a
  node somebody typed into a form, so a TTL would delete it minutes after
  it was added — which reads as the product losing the entry.
* **It is probed instead.** An unreachable pinned node stays on the page
  and turns red, rather than vanishing. "It is down" is a far better
  answer than "it is gone".

The id collision tests matter more than they look: ids namespace the
Redis concurrency gauge, so two backends sharing one would each appear
half as loaded as they are.
"""

import pytest

from app.db.sql.inference_nodes import InferenceNodeDB
from app.services.ai.schemas import HealthState, InferenceBackend, NodeHealth
from tests.conftest import auth_header, create_org, create_user, make_token

NODE = {
    'node_id': 'gpu-hand-01',
    'base_url': 'http://gpu-hand-01.internal:8000/v1',
    'models': ['igni-1.0-27b'],
    'max_concurrent': 32,
}


@pytest.fixture
async def admin(db):
    # Changing the shared fleet is a super admin's (SER-32).
    await create_user(db, user_id='admin-1', email='admin@test.com', is_super_admin=True)
    org = await create_org(db, owner_user_id='admin-1', slug='acme', display_name='Acme')
    return auth_header(make_token('admin-1', org_id=str(org.id)))


@pytest.fixture
async def member(db):
    await create_user(db, user_id='admin-1', email='admin@test.com')
    await create_user(db, user_id='dev-1', email='dev@test.com')
    org = await create_org(db, owner_user_id='admin-1', slug='acme', display_name='Acme')
    from tests.conftest import add_member

    await add_member(db, org_id=str(org.id), user_id='dev-1', role='member')
    return auth_header(make_token('dev-1', org_id=str(org.id)))


@pytest.fixture(autouse=True)
def no_probing(monkeypatch):
    """Nodes in these tests have addresses that do not resolve.

    Without this the page render would spend the probe timeout on each of
    them, and the tests would be measuring httpx rather than the feature.
    """

    async def _all(self, backends):
        return {b.id: NodeHealth(state=HealthState.HEALTHY) for b in backends}

    monkeypatch.setattr('app.services.ai.health.NodeHealthProbe.check_all', _all)


@pytest.fixture(autouse=True)
def clean_pool(monkeypatch, tmp_path):
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
    monkeypatch.setattr('app.api.base_v1.endpoints.portal.inference.NodeRegistry', NoRegistry)
    yield
    SingletonMeta._instances.pop(PoolManager, None)


class TestReadingOneBack:
    """`GET /portal/inference/nodes/{id}` — what the edit form loads.

    The fleet list used to be editable in place, two checkboxes that
    PATCHed on change. Editing moved to a page of its own, and a form
    needs the row **as stored**: `GET /inference` reports what the fleet
    is *doing* — load, health, heartbeats — and carries no `enabled`
    at all, so a form built from it would drop a field on every save.
    """

    async def test_an_admin_gets_the_stored_definition(self, client, admin):
        await client.post('/portal/inference/nodes', json=NODE, headers=admin)

        resp = await client.get('/portal/inference/nodes/gpu-hand-01', headers=admin)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body['node_id'] == 'gpu-hand-01'
        assert body['base_url'] == NODE['base_url']
        assert body['models'] == NODE['models']
        assert body['max_concurrent'] == 32
        assert 'pools' not in body
        # The field the status view does not have, which is the reason
        # this endpoint exists.
        assert body['enabled'] is True

    async def test_the_api_key_never_comes_back(self, client, admin):
        """`has_api_key` instead, so the form can say "stored — leave
        blank to keep it" without the credential crossing the wire."""
        await client.post('/portal/inference/nodes', json={**NODE, 'api_key': 'a-real-key'}, headers=admin)

        body = (await client.get('/portal/inference/nodes/gpu-hand-01', headers=admin)).json()

        assert body['has_api_key'] is True
        assert 'a-real-key' not in str(body)
        assert 'api_key' not in body

    async def test_a_node_defined_elsewhere_is_a_404(self, client, admin):
        """A registered node is defined by whatever started the cluster
        and a static one by the config file. Offering a form that cannot
        save is worse than offering none, so the portal is told plainly
        rather than shown an editable copy of something it cannot
        change."""
        resp = await client.get('/portal/inference/nodes/not-added-here', headers=admin)

        assert resp.status_code == 404, resp.text
        assert 'added here' in resp.json()['detail']

    async def test_a_member_cannot_read_it(self, client, member):
        """It carries the node's address and whether a credential is
        set, which is fleet configuration rather than something every
        developer needs."""
        resp = await client.get('/portal/inference/nodes/gpu-hand-01', headers=member)

        assert resp.status_code == 403


class TestAdding:
    async def test_an_admin_can_add_a_node(self, client, admin):
        resp = await client.post('/portal/inference/nodes', json=NODE, headers=admin)
        assert resp.status_code == 201
        assert resp.json()['node_id'] == 'gpu-hand-01'

    async def test_a_member_cannot(self, client, member):
        """The write path adds a machine every developer's prompts get
        sent to."""
        resp = await client.post('/portal/inference/nodes', json=NODE, headers=member)
        assert resp.status_code == 403

    async def test_it_appears_in_the_pool_immediately(self, client, admin):
        """No restart, and no waiting out the provider's reload interval —
        this process just changed the fleet, so it invalidates its own
        cache."""
        await client.post('/portal/inference/nodes', json=NODE, headers=admin)

        resp = await client.get('/portal/inference', headers=admin)
        chat = resp.json()
        assert [n['id'] for n in chat['nodes']] == ['gpu-hand-01']

    async def test_it_is_labelled_pinned(self, client, admin):
        """Distinct from static and registered, because the three are
        changed in three different places."""
        await client.post('/portal/inference/nodes', json=NODE, headers=admin)

        resp = await client.get('/portal/inference', headers=admin)
        chat = resp.json()
        assert chat['nodes'][0]['source'] == 'pinned'

    async def test_the_credential_is_never_returned(self, client, admin):
        """It goes in and stays in. An admin who needs to change it sets a
        new one."""
        resp = await client.post('/portal/inference/nodes', json={**NODE, 'api_key': 'node-secret'}, headers=admin)
        assert 'node-secret' not in resp.text
        assert resp.json()['has_api_key'] is True

    async def test_a_relative_base_url_is_refused(self, client, admin):
        resp = await client.post('/portal/inference/nodes', json={**NODE, 'base_url': 'gpu:8000'}, headers=admin)
        assert resp.status_code == 422

    async def test_an_id_with_a_colon_is_refused(self, client, admin):
        """Ids namespace Redis keys."""
        resp = await client.post('/portal/inference/nodes', json={**NODE, 'node_id': 'a:b'}, headers=admin)
        assert resp.status_code == 422


class TestIdCollisions:
    """One machine must not end up with two entries."""

    async def test_a_duplicate_is_refused(self, client, admin):
        await client.post('/portal/inference/nodes', json=NODE, headers=admin)
        resp = await client.post('/portal/inference/nodes', json=NODE, headers=admin)
        assert resp.status_code == 409
        assert 'already been added' in resp.json()['detail']

    async def test_a_statically_configured_id_is_refused(self, client, admin, monkeypatch):
        from app.services.ai.pool_manager import PoolManager

        monkeypatch.setattr(PoolManager().provider, 'static_backend_ids', lambda: {'gpu-hand-01'})
        resp = await client.post('/portal/inference/nodes', json=NODE, headers=admin)
        assert resp.status_code == 409
        assert 'static configuration' in resp.json()['detail']

    async def test_a_registered_id_is_refused(self, client, admin, monkeypatch):
        """Otherwise the same GPU is both self-registering and pinned, and
        its concurrency gauge is shared between two entries."""

        class Registered:
            async def list_nodes(self):
                return []

            async def get(self, node_id):
                return object() if node_id == 'gpu-hand-01' else None

        monkeypatch.setattr('app.api.base_v1.endpoints.portal.inference.NodeRegistry', Registered)
        resp = await client.post('/portal/inference/nodes', json=NODE, headers=admin)
        assert resp.status_code == 409
        assert 'cluster automation' in resp.json()['detail']


class TestEditing:
    async def test_a_node_can_be_repointed(self, client, admin):
        await client.post('/portal/inference/nodes', json=NODE, headers=admin)
        resp = await client.patch(
            '/portal/inference/nodes/gpu-hand-01',
            json={'base_url': 'http://moved.internal:8000/v1'},
            headers=admin,
        )
        assert resp.status_code == 200
        assert resp.json()['base_url'] == 'http://moved.internal:8000/v1'

    async def test_omitting_the_key_leaves_it_alone(self, client, admin):
        """The portal never sends the stored key back, so treating absent
        as "clear" would wipe the credential on every unrelated edit."""
        await client.post('/portal/inference/nodes', json={**NODE, 'api_key': 'keep-me'}, headers=admin)
        await client.patch('/portal/inference/nodes/gpu-hand-01', json={'model': 'other'}, headers=admin)

        stored = await InferenceNodeDB().get_by_node_id('gpu-hand-01')
        assert stored is not None
        assert InferenceNodeDB().to_backend(stored).api_key == 'keep-me'

    async def test_an_empty_key_clears_it(self, client, admin):
        """The explicit way to remove a credential."""
        await client.post('/portal/inference/nodes', json={**NODE, 'api_key': 'remove-me'}, headers=admin)
        await client.patch('/portal/inference/nodes/gpu-hand-01', json={'api_key': ''}, headers=admin)

        stored = await InferenceNodeDB().get_by_node_id('gpu-hand-01')
        assert stored is not None
        assert stored.api_key is None

    async def test_disabling_takes_it_out_of_rotation_without_deleting_it(self, client, admin):
        """For a node being worked on: keep the entry, stop routing."""
        await client.post('/portal/inference/nodes', json=NODE, headers=admin)
        await client.patch('/portal/inference/nodes/gpu-hand-01', json={'enabled': False}, headers=admin)

        resp = await client.get('/portal/inference', headers=admin)
        chat = resp.json()
        assert chat['nodes'] == []

    async def test_editing_something_absent_is_a_404(self, client, admin):
        resp = await client.patch('/portal/inference/nodes/never-added', json={'model': 'x'}, headers=admin)
        assert resp.status_code == 404

    async def test_a_member_cannot_edit(self, client, member):
        resp = await client.patch('/portal/inference/nodes/gpu-hand-01', json={'model': 'x'}, headers=member)
        assert resp.status_code == 403


class TestTheKeyIsEncryptedAtRest:
    """F150. The node's proxy key was stored as given, so a backup or a
    read-only SQL session carried a working credential. It is a Fernet
    ciphertext under ``SECRET_ENCRYPTION_KEY`` now, decrypted only to
    route (and rotated by ``tools/rotate_secret_key.py``)."""

    async def test_the_stored_value_is_a_ciphertext(self, client, admin):
        await client.post('/portal/inference/nodes', json={**NODE, 'api_key': 'node-secret'}, headers=admin)

        stored = await InferenceNodeDB().get_by_node_id('gpu-hand-01')

        assert stored is not None and stored.api_key
        assert 'node-secret' not in str(stored.api_key)
        assert str(stored.api_key).startswith('gAAAAA')

    async def test_routing_gets_the_key_itself(self, client, admin):
        await client.post('/portal/inference/nodes', json={**NODE, 'api_key': 'node-secret'}, headers=admin)
        await client.patch('/portal/inference/nodes/gpu-hand-01', json={'api_key': 'a-new-one'}, headers=admin)

        from app.services.ai.pool_manager import PoolManager

        backends = await PoolManager().provider.load()

        (node,) = [b for b in backends if b.id == 'gpu-hand-01']
        assert node.api_key == 'a-new-one'
        stored = await InferenceNodeDB().get_by_node_id('gpu-hand-01')
        assert stored is not None and 'a-new-one' not in str(stored.api_key)

    async def test_a_key_that_will_not_decrypt_costs_that_node_only(self, client, admin, monkeypatch):
        """Rotated without keeping the old key: routing to the node without
        its key would fail every request with the node's 401, so it is
        left out — and the others still route."""
        from cryptography.fernet import Fernet

        from app.config import settings
        from app.services.ai.pool_manager import PoolManager

        await client.post('/portal/inference/nodes', json={**NODE, 'api_key': 'old-key-secret'}, headers=admin)
        monkeypatch.setattr(settings, 'secret_encryption_key', Fernet.generate_key().decode())
        monkeypatch.setattr(settings, 'secret_encryption_keys_previous', '')
        await client.post(
            '/portal/inference/nodes', json={**NODE, 'node_id': 'gpu-hand-02', 'api_key': 'new'}, headers=admin
        )

        backends = await PoolManager().provider.load()

        assert [b.id for b in backends] == ['gpu-hand-02']


class TestRemoving:
    async def test_a_node_can_be_removed(self, client, admin):
        await client.post('/portal/inference/nodes', json=NODE, headers=admin)
        assert (await client.delete('/portal/inference/nodes/gpu-hand-01', headers=admin)).status_code == 204

        resp = await client.get('/portal/inference', headers=admin)
        chat = resp.json()
        assert chat['nodes'] == []

    async def test_removing_twice_still_succeeds(self, client, admin):
        await client.post('/portal/inference/nodes', json=NODE, headers=admin)
        assert (await client.delete('/portal/inference/nodes/gpu-hand-01', headers=admin)).status_code == 204
        assert (await client.delete('/portal/inference/nodes/gpu-hand-01', headers=admin)).status_code == 204

    async def test_a_member_cannot_remove(self, client, member):
        resp = await client.delete('/portal/inference/nodes/gpu-hand-01', headers=member)
        assert resp.status_code == 403


class TestItNeverDisappears:
    """The property that made this a table rather than another TTL key."""

    async def test_an_unreachable_node_is_still_listed(self, client, admin, monkeypatch):
        async def _all(self, backends: list[InferenceBackend]):
            return {b.id: NodeHealth(state=HealthState.UNREACHABLE, detail='No response within 3s') for b in backends}

        monkeypatch.setattr('app.services.ai.health.NodeHealthProbe.check_all', _all)
        await client.post('/portal/inference/nodes', json=NODE, headers=admin)

        resp = await client.get('/portal/inference', headers=admin)
        chat = resp.json()
        node = chat['nodes'][0]

        assert node['id'] == 'gpu-hand-01'
        assert node['health']['state'] == 'unreachable'
        assert node['health']['detail'] == 'No response within 3s'


class TestDiscovery:
    """Paste an address; the node says what it is.

    The reason this exists: typing model ids by hand is how you end up
    with a node that is never selected. A mistyped id does not error —
    nothing ever matches it — and LoRA adapter names are whatever the
    operator passed to ``--lora-modules``, which nobody remembers.
    """

    @pytest.fixture
    def answers(self, monkeypatch):
        from app.services.ai.schemas import ProbedNode

        def _with(models: list[str], state=HealthState.HEALTHY, detail=None):
            async def _list(self, *, base_url, api_key=None):
                return ProbedNode(health=NodeHealth(state=state, detail=detail, latency_ms=9.0), models=models)

            monkeypatch.setattr('app.services.ai.health.NodeHealthProbe.list_models', _list)

        return _with

    async def test_it_returns_what_the_node_serves(self, client, admin, answers):
        answers(['base-27b', 'sql-expert', 'docs-writer'])
        resp = await client.post(
            '/portal/inference/nodes/discover',
            json={'base_url': 'http://gpu-01.internal:8000/v1'},
            headers=admin,
        )
        assert resp.status_code == 200
        assert resp.json()['reachable'] is True
        assert resp.json()['models'] == ['base-27b', 'sql-expert', 'docs-writer']

    async def test_lora_adapters_come_back_alongside_the_base_model(self, client, admin, answers):
        """A vLLM node with --enable-lora lists them all here, which is the
        only practical way to learn the names."""
        answers(['base-27b', 'sql-expert'])
        resp = await client.post(
            '/portal/inference/nodes/discover',
            json={'base_url': 'http://gpu-01.internal:8000/v1'},
            headers=admin,
        )
        assert 'sql-expert' in resp.json()['models']

    async def test_an_unreachable_address_is_a_200_with_the_reason(self, client, admin, answers):
        """ "I could not reach that" is a successful answer to "what is at
        this address" — the dialog needs the reason to show, not an
        exception."""
        answers([], state=HealthState.UNREACHABLE, detail='No response within 3s')
        resp = await client.post(
            '/portal/inference/nodes/discover',
            json={'base_url': 'http://nothing.internal:8000/v1'},
            headers=admin,
        )
        assert resp.status_code == 200
        assert resp.json()['reachable'] is False
        assert resp.json()['detail'] == 'No response within 3s'

    async def test_a_relative_url_is_refused(self, client, admin):
        resp = await client.post('/portal/inference/nodes/discover', json={'base_url': 'gpu:8000'}, headers=admin)
        assert resp.status_code == 422

    async def test_a_member_cannot_probe_the_network(self, client, member):
        """The endpoint makes this deployment issue an HTTP request to an
        address the caller chose."""
        resp = await client.post(
            '/portal/inference/nodes/discover',
            json={'base_url': 'http://gpu-01.internal:8000/v1'},
            headers=member,
        )
        assert resp.status_code == 403


class TestModelMismatch:
    async def test_a_node_not_serving_its_configured_model_is_degraded(self, client, admin, monkeypatch):
        """Otherwise it sits on the page looking healthy while every
        request for that model reports "not served by this deployment"."""

        async def _all(self, backends):
            return {
                b.id: NodeHealth(
                    state=HealthState.DEGRADED,
                    detail='Does not serve igni-1.0-27b; it offers other-model',
                )
                for b in backends
            }

        monkeypatch.setattr('app.services.ai.health.NodeHealthProbe.check_all', _all)
        await client.post('/portal/inference/nodes', json=NODE, headers=admin)

        resp = await client.get('/portal/inference', headers=admin)
        chat = resp.json()
        assert chat['nodes'][0]['health']['state'] == 'degraded'
        assert 'Does not serve' in chat['nodes'][0]['health']['detail']


class TestModelsForTheGroupPicker:
    """``GET /portal/inference/models``.

    A portal-authenticated twin of ``/chat/models``. The portal must not
    call the client endpoint: that one authenticates a real user JWT, the
    portal session is a different scheme, and the 401 it returns is
    indistinguishable in the browser from an expired session — so asking
    for it logged the admin out.
    """

    @pytest.fixture(autouse=True)
    def served(self, monkeypatch):
        async def _list(self):
            return [
                ('igni-1.0-27b', 'http://gpu-01:8000/v1'),
                ('legal-reviewer', 'http://gpu-01:8000/v1'),
                # The same model on a second node must not appear twice.
                ('igni-1.0-27b', 'http://gpu-02:8000/v1'),
            ]

        monkeypatch.setattr('app.services.ai.backend_pool.InferenceBackendPool.list_available_models', _list)

    async def test_it_lists_every_served_model_once(self, client, admin):
        resp = await client.get('/portal/inference/models', headers=admin)
        assert resp.status_code == 200
        assert resp.json()['models'] == ['igni-1.0-27b', 'legal-reviewer']

    async def test_adapters_are_offered_too(self, client, admin):
        """A node answers to each adapter by name, so any of them is a
        legitimate choice for a group."""
        resp = await client.get('/portal/inference/models', headers=admin)
        assert 'legal-reviewer' in resp.json()['models']

    async def test_a_member_cannot_enumerate_them(self, client, member):
        resp = await client.get('/portal/inference/models', headers=member)
        assert resp.status_code == 403

    async def test_no_gpus_yet_is_an_empty_list_not_an_error(self, client, admin, monkeypatch):
        """A deployment that has configured nothing is a state to render."""
        from app.services.ai.backend_provider import BackendConfigError

        async def _boom(self):
            raise BackendConfigError('nothing configured')

        monkeypatch.setattr('app.services.ai.backend_pool.InferenceBackendPool.list_available_models', _boom)
        resp = await client.get('/portal/inference/models', headers=admin)
        assert resp.status_code == 200
        assert resp.json()['models'] == []
