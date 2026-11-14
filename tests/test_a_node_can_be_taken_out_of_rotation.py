"""Stopping a GPU without removing it, and having it stay stopped.

The portal could remove a node and it could edit one, and neither is the
thing an operator wants during an incident. Removal is destructive and
only worked for nodes added from the portal. Editing needed a form, and a
self-registered node had no editable state at all — its "Serves" field
read *"set where this node is defined"*, because registration is
idempotent and the cluster re-announces itself every 60 seconds, so
anything the portal wrote would be overwritten inside three minutes.

The two properties that make a drain worth having, rather than a switch
that looks like one:

* **it outlives the thing it overrides.** A node restarting, or
  re-registering, must not quietly resume taking traffic. That is the
  failure that made this read-only, so a fix that reproduced it would be
  worse than no fix;
* **the node stays visible.** A switch whose subject vanishes cannot be
  switched back, and a drained node dropped from the portal is a GPU
  nobody can turn on again without a redeploy.

And one that makes it usable: it stops *new* work only. Drain, watch
in-flight fall to zero on the row, then pull the machine.
"""

from __future__ import annotations

import pytest

from app.services.ai.backend import InferenceBackend
from app.services.ai.backend_provider import BackendConfigError
from app.services.ai.node_drain import NodeDrainRegistry
from app.services.ai.node_registry import NodeRegistry
from app.services.ai.pool_manager import PoolManager
from app.services.ai.schemas import AllBackendsDrainedError
from tests.conftest import add_member, audit_rows, auth_header, create_org, create_user, make_token

pytestmark = pytest.mark.anyio

_ABSOLUTE = 'http://test'


def _backend(node_id: str, *, port: int = 9001) -> InferenceBackend:
    return InferenceBackend(
        id=node_id,
        base_url=f'http://127.0.0.1:{port}',
        models=['test-model'],
        max_concurrent=4,
    )


@pytest.fixture(autouse=True)
async def _leave_the_fleet_as_we_found_it():
    """Registrations, drains **and leases** are global state in Redis.

    The leases are the part that bit. Without clearing them, one test's
    in-flight counts survived into the next, so `_least_loaded` had a
    reason to skip `gpu-a` that had nothing to do with draining — and a
    revert-check with the drain filter deleted still passed. The test was
    asserting leaked load.

    Cleaned per test rather than per write, so a test that fails midway
    leaves nothing behind either.
    """
    yield

    import redis.asyncio as aioredis

    from app.config import settings
    from app.db.redis.keys import key as redis_key

    registry, drains = NodeRegistry(), NodeDrainRegistry()
    for node in await registry.list_nodes():
        await registry.deregister(node.backend.id)
    for node_id in await drains.drained_ids():
        await drains.resume(node_id)

    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        for kind in ('inflight', 'sticky'):
            pattern = redis_key('backends', kind) + ':*'
            keys = [key async for key in redis_client.scan_iter(match=pattern, count=500)]
            if keys:
                await redis_client.delete(*keys)
    finally:
        await redis_client.aclose()

    PoolManager().provider.invalidate()


@pytest.fixture
async def two_nodes():
    """Two registered nodes serving chat, and nothing drained."""
    registry = NodeRegistry()
    await registry.register(_backend('gpu-a', port=9001))
    await registry.register(_backend('gpu-b', port=9002))
    PoolManager().provider.invalidate()
    return ['gpu-a', 'gpu-b']


class TestDrainingStopsSelection:
    # Both directions, because the first version of this test drained
    # `gpu-a` and asserted only `gpu-b` was chosen — and it passed with
    # the drain filter deleted. On an idle two-node pool `_least_loaded`
    # breaks the tie deterministically, so it was asserting that tie, not
    # the drain. Parametrised so a broken filter has to fail one of them.
    @pytest.mark.parametrize('stopped, expected', [('gpu-a', 'gpu-b'), ('gpu-b', 'gpu-a')])
    async def test_a_drained_node_is_never_chosen(self, two_nodes, stopped, expected):
        pool = PoolManager().get_pool()
        await NodeDrainRegistry().drain(stopped, by='admin-1')

        # Enough attempts that stickiness and least-loaded both get a
        # turn: one lease could land on the survivor by luck.
        chosen = set()
        for index in range(8):
            lease = await pool.acquire(f'user-{index}')
            chosen.add(lease.backend_id)
            await pool.release(lease)

        assert chosen == {expected}, chosen

    async def test_it_applies_without_waiting_for_a_cache(self, two_nodes):
        """Read on every acquire rather than baked into the cached
        backend list.

        A drain that took effect when a cache happened to turn over
        would be useless for the case it exists for — the operator is
        watching, and the next request is the one that matters.
        """
        pool = PoolManager().get_pool()
        # Warm the provider cache first, so the drain is the only thing
        # that changes between the two acquires.
        first = await pool.acquire('u')
        await pool.release(first)

        await NodeDrainRegistry().drain(first.backend_id, by='admin-1')

        second = await pool.acquire('u')
        await pool.release(second)

        assert second.backend_id != first.backend_id

    async def test_a_resumed_node_is_chosen_again(self, two_nodes):
        """Asserted by draining the *other* node, so the resumed one is
        the only candidate.

        The first version expected leases to land on both nodes, which
        is not a property this pool has: `_least_loaded` compares with a
        strict `<`, so an idle tie always goes to the first candidate.
        That test was asserting the tie-break, and it failed as soon as
        the leaked lease state that had been skewing it was cleaned up.
        """
        drains = NodeDrainRegistry()
        pool = PoolManager().get_pool()

        await drains.drain('gpu-a', by='admin-1')
        assert await drains.resume('gpu-a') is True
        await drains.drain('gpu-b', by='admin-1')

        lease = await pool.acquire('u')
        await pool.release(lease)

        assert lease.backend_id == 'gpu-a'

    async def test_draining_everything_says_so_rather_than_claiming_nothing_is_configured(self, two_nodes):
        """The fix for each is entirely different.

        "No inference backend is configured" sends an administrator who
        drained the last node looking at their deployment config. Naming
        the drained nodes turns that into a two-second fix.
        """
        drains = NodeDrainRegistry()
        for node_id in two_nodes:
            await drains.drain(node_id, by='admin-1')

        with pytest.raises(AllBackendsDrainedError) as caught:
            await PoolManager().get_pool().acquire('u')

        assert 'gpu-a' in str(caught.value) and 'gpu-b' in str(caught.value)
        assert 'drained' in str(caught.value)

    async def test_an_actually_empty_deployment_still_says_that(self):
        """The new error must not swallow the old one."""
        PoolManager().provider.invalidate()

        with pytest.raises(BackendConfigError) as caught:
            await PoolManager().get_pool().acquire('u')

        assert not isinstance(caught.value, AllBackendsDrainedError), caught.value

    async def test_a_drained_node_stops_advertising_its_models(self, two_nodes):
        """The CLI asks what it may request. Offering a model only a
        drained node serves produces a request that cannot be routed."""
        registry = NodeRegistry()
        await registry.register(
            InferenceBackend(
                id='gpu-only-me',
                base_url='http://127.0.0.1:9003',
                models=['exclusive-model'],
                max_concurrent=1,
            )
        )
        PoolManager().provider.invalidate()
        pool = PoolManager().get_pool()

        before = {name for name, _ in await pool.list_available_models()}
        assert 'exclusive-model' in before

        await NodeDrainRegistry().drain('gpu-only-me', by='admin-1')

        after = {name for name, _ in await pool.list_available_models()}
        assert 'exclusive-model' not in after
        assert 'test-model' in after, 'the rest of the fleet is still advertised'


class TestTheDrainOutlivesTheRegistration:
    async def test_the_record_has_no_expiry_at_all(self, two_nodes):
        """Asserted on the key's TTL, not by waiting.

        The first version of this file tested "outlives a
        re-registration" by re-registering immediately — which passes
        even with a one-second expiry, as a revert-check proved. A drain
        is meant to last until somebody clears it, and the only way to
        assert *that* in a fast test is to look at whether an expiry
        exists: -1 is "no TTL", -2 is "no key".
        """
        import redis.asyncio as aioredis

        from app.config import settings
        from app.db.redis.keys import key as redis_key

        await NodeDrainRegistry().drain('gpu-a', by='admin-1')

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            ttl = await redis_client.ttl(redis_key('nodes', 'drained') + ':gpu-a')
        finally:
            await redis_client.aclose()

        assert ttl == -1, f'the drain expires in {ttl}s; it must last until somebody clears it'

    async def test_re_registering_does_not_resume_it(self, two_nodes):
        """The property the whole design turns on.

        A registration is the *node's* statement about itself and it has
        a TTL; a drain is the *operator's* statement about that node and
        it has none. Stored together, a node restart would silently send
        traffic back to a GPU somebody deliberately stopped — which is
        the exact reason this field was read-only before.
        """
        await NodeDrainRegistry().drain('gpu-a', by='admin-1')

        # The cluster comes back and re-announces itself, replacing the
        # registration entirely.
        await NodeRegistry().register(_backend('gpu-a', port=9001))
        PoolManager().provider.invalidate()

        assert 'gpu-a' in await NodeDrainRegistry().drained_ids()

        chosen = set()
        for index in range(8):
            lease = await PoolManager().get_pool().acquire(f'user-{index}')
            chosen.add(lease.backend_id)
            await PoolManager().get_pool().release(lease)
        assert chosen == {'gpu-b'}, chosen

    async def test_a_heartbeat_does_not_resume_it(self, two_nodes):
        await NodeDrainRegistry().drain('gpu-a', by='admin-1')

        assert await NodeRegistry().heartbeat('gpu-a') is not None
        assert 'gpu-a' in await NodeDrainRegistry().drained_ids()


class TestTheNodeStaysVisible:
    async def test_a_drained_node_is_still_listed_with_its_reason(self, client, admin, two_nodes):
        """Otherwise the switch cannot be switched back."""
        await client.post(
            '/portal/inference/nodes/gpu-a/drain',
            headers=auth_header(admin['token']),
            json={'reason': 'firmware update'},
        )

        response = await client.get('/portal/inference', headers=auth_header(admin['token']))
        assert response.status_code == 200, response.text

        chat = response.json()
        by_id = {node['id']: node for node in chat['nodes']}

        assert 'gpu-a' in by_id, 'the drained node disappeared from the portal'
        assert by_id['gpu-a']['drain'] is not None
        assert by_id['gpu-a']['drain']['reason'] == 'firmware update'
        assert by_id['gpu-a']['drain']['by_email'] == admin['email']
        assert by_id['gpu-b']['drain'] is None

    async def test_the_totals_do_not_count_a_drained_node(self, client, admin, two_nodes):
        """`total_capacity` is the number an operator reads to decide
        whether they need another GPU. A node that cannot take work is
        not capacity."""
        before = await client.get('/portal/inference', headers=auth_header(admin['token']))
        capacity_before = before.json()['total_capacity']

        await client.post('/portal/inference/nodes/gpu-a/drain', headers=auth_header(admin['token']), json={})

        after = await client.get('/portal/inference', headers=auth_header(admin['token']))
        chat = after.json()

        assert chat['total_capacity'] == capacity_before - 4
        assert len(chat['nodes']) == 2, 'it should be listed but not counted'


class TestTheEndpoints:
    async def test_a_plain_member_cannot_drain(self, client, db, two_nodes):
        user = await create_user(db, email='member-drain@example.com')
        org = await create_org(db, owner_user_id=str(user.user_id), slug='drain-org')
        await add_member(db, str(org.id), str(user.user_id), role='member')
        await db.commit()
        token = make_token(str(user.user_id), org_id=str(org.id), org_name='D')

        response = await client.post('/portal/inference/nodes/gpu-a/drain', headers=auth_header(token), json={})

        assert response.status_code == 403, response.text
        assert await NodeDrainRegistry().drained_ids() == set()

    async def test_draining_an_unknown_node_is_a_404(self, client, admin, two_nodes):
        """A drain on a typo applies to nothing and looks like it
        worked, which is the worst of the three outcomes."""
        response = await client.post(
            '/portal/inference/nodes/gpu-typo/drain', headers=auth_header(admin['token']), json={}
        )

        assert response.status_code == 404, response.text
        assert await NodeDrainRegistry().drained_ids() == set()

    async def test_resuming_something_not_drained_is_success(self, client, admin, two_nodes):
        """The caller's goal — this node can serve — holds either way."""
        response = await client.delete('/portal/inference/nodes/gpu-a/drain', headers=auth_header(admin['token']))

        assert response.status_code == 204, response.text

    async def test_a_drain_left_by_a_node_that_is_gone_can_be_cleared(self, client, admin):
        """Resume deliberately does not check the node exists.

        A drain has no expiry, so a drained node that is removed and
        never comes back leaves its record behind. Clearing that is
        exactly the tidy-up somebody would want, and refusing it because
        the node is missing would make the record unclearable.
        """
        await NodeDrainRegistry().drain('gpu-long-gone', by='admin-1')

        response = await client.delete(
            '/portal/inference/nodes/gpu-long-gone/drain', headers=auth_header(admin['token'])
        )

        assert response.status_code == 204, response.text
        assert await NodeDrainRegistry().drained_ids() == set()

    async def test_it_is_audited_as_an_action_on_the_node(self, client, admin, two_nodes):
        """It changes where prompts go, which is F114's whole subject.

        Falls out of F90 auditing by route and method, and is asserted
        rather than assumed — including that the target is the node,
        since that is what an administrator would search by.
        """

        await client.post(
            '/portal/inference/nodes/gpu-a/drain',
            headers=auth_header(admin['token']),
            json={'reason': 'noisy neighbour'},
        )

        rows = [r for r in audit_rows(method='POST') if r['route'].endswith('/drain')]

        assert len(rows) == 1, rows
        assert rows[0]['target_type'] == 'inference_node'
        assert rows[0]['target_id'] == 'gpu-a'
        assert rows[0]['outcome'] == 'success'


class TestTheDrainRegistryItself:
    async def test_it_records_who_and_when(self):
        record = await NodeDrainRegistry().drain('gpu-x', by='u-1', by_email='a@example.com', reason='why')

        stored = (await NodeDrainRegistry().all())['gpu-x']
        assert stored.by == 'u-1'
        assert stored.by_email == 'a@example.com'
        assert stored.reason == 'why'
        assert stored.at == record.at

    async def test_an_unreadable_record_keeps_the_node_drained(self):
        """Draining is a restriction, so a record that cannot be parsed
        is kept rather than discarded — forgetting it would send traffic
        to a node somebody deliberately stopped."""
        import redis.asyncio as aioredis

        from app.config import settings
        from app.db.redis.keys import key as redis_key

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            await redis_client.set(redis_key('nodes', 'drained') + ':gpu-corrupt', 'not json at all')

            drains = await NodeDrainRegistry().all()
            assert 'gpu-corrupt' in drains
            assert 'could not be read' in (drains['gpu-corrupt'].reason or '')
            assert 'gpu-corrupt' in await NodeDrainRegistry().drained_ids()
        finally:
            await redis_client.delete(redis_key('nodes', 'drained') + ':gpu-corrupt')
            await redis_client.aclose()

    async def test_it_fails_open_when_redis_is_unreachable(self, monkeypatch):
        """The uncomfortable choice of the two, made deliberately.

        Failing closed would mean a Redis blip drains the whole fleet and
        every chat request answers "nothing is configured" — a cache
        outage becoming a total one. A GPU briefly serving requests it
        should not is recoverable.
        """
        registry = NodeDrainRegistry()

        async def refuse():
            raise ConnectionError('redis is gone')

        monkeypatch.setattr(registry, '_get_redis', refuse)

        assert await registry.drained_ids() == set()


@pytest.fixture
async def admin(db):
    email = 'drain-admin@example.com'
    # Draining the shared fleet is a super admin's (SER-32).
    user = await create_user(db, email=email, is_super_admin=True)
    org = await create_org(db, owner_user_id=str(user.user_id), slug='drain-admin-org')
    await add_member(db, str(org.id), str(user.user_id), role='admin')
    await db.commit()
    return {
        'email': email,
        'token': make_token(str(user.user_id), org_id=str(org.id), org_name='A'),
    }
