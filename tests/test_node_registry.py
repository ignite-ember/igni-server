"""Nodes that register themselves.

The behaviour worth defending is what happens when a node does **not**
shut down politely. A stop-cluster job runs the deregister call; a spot
reclaim, a kernel panic and a severed network do not, and those are the
ones that hurt — a node still in rotation that no longer answers fails
every request routed to it. Expiry is what makes the ugly case converge
on the tidy one, so most of this file is about the TTL.

The second theme is that the registry must never be able to break
routing. It is best-effort infrastructure sitting in front of the
customer's GPUs, and a Redis blip should cost them their burst nodes, not
their inference.
"""

import json

import pytest

from app.services.ai.backend import InferenceBackend
from app.services.ai.backend_provider import BackendConfigError, InferenceBackendProvider
from app.services.ai.node_registry import NodeRegistry
from app.services.ai.schemas import RegisteredNode


class FakeRedis:
    """Strings with a recorded TTL, plus scan and mget."""

    def __init__(self):
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None):
        self.values[key] = value
        self.ttls[key] = ex

    async def delete(self, key):
        return 1 if self.values.pop(key, None) is not None else 0

    async def mget(self, keys):
        return [self.values.get(key) for key in keys]

    async def scan_iter(self, match=None, count=None):
        prefix = (match or '').replace('*', '')
        for key in list(self.values.keys()):
            if key.startswith(prefix):
                yield key

    def expire_key(self, node_id: str) -> None:
        """What Redis would do on its own, on demand."""
        self.values.pop(f'igni:server:nodes:registered:{node_id}', None)


@pytest.fixture
def registry_and_redis():
    registry = NodeRegistry()
    redis = FakeRedis()

    async def _stub():
        return redis

    registry._get_redis = _stub  # type: ignore[method-assign]
    return registry, redis


def _backend(node_id='gpu-burst-01', **overrides) -> InferenceBackend:
    return InferenceBackend(
        id=node_id,
        base_url=overrides.get('base_url', f'http://{node_id}.internal:8000/v1'),
        model=overrides.get('model', 'igni-1.0-27b'),
        max_concurrent=overrides.get('max_concurrent', 64),
    )


class TestRegistration:
    async def test_a_registered_node_is_listed(self, registry_and_redis):
        registry, _ = registry_and_redis
        await registry.register(_backend())
        assert [n.backend.id for n in await registry.list_nodes()] == ['gpu-burst-01']

    async def test_registration_carries_a_ttl(self, registry_and_redis):
        """Without one the registry would be a list of machines that were
        once alive, which is worse than no list at all."""
        registry, redis = registry_and_redis
        await registry.register(_backend())
        assert redis.ttls['igni:server:nodes:registered:gpu-burst-01'] > 0

    async def test_re_registering_replaces_rather_than_fails(self, registry_and_redis):
        """A node that restarts and re-announces is normal, and this is
        also how it moves to a new address after being rescheduled."""
        registry, _ = registry_and_redis
        await registry.register(_backend())
        await registry.register(_backend(base_url='http://moved.internal:8000/v1'))
        nodes = await registry.list_nodes()
        assert len(nodes) == 1
        assert nodes[0].backend.base_url == 'http://moved.internal:8000/v1'

    async def test_nodes_are_listed_in_a_stable_order(self, registry_and_redis):
        """The portal's node table should not reshuffle between refreshes."""
        registry, _ = registry_and_redis
        for node_id in ('gpu-03', 'gpu-01', 'gpu-02'):
            await registry.register(_backend(node_id))
        assert [n.backend.id for n in await registry.list_nodes()] == ['gpu-01', 'gpu-02', 'gpu-03']


class TestHeartbeat:
    async def test_a_heartbeat_refreshes_the_clock(self, registry_and_redis):
        registry, redis = registry_and_redis
        await registry.register(_backend())
        first = (await registry.get('gpu-burst-01')).last_seen

        stored = json.loads(redis.values['igni:server:nodes:registered:gpu-burst-01'])
        stored['last_seen'] = first - 100
        redis.values['igni:server:nodes:registered:gpu-burst-01'] = json.dumps(stored)

        refreshed = await registry.heartbeat('gpu-burst-01')
        assert refreshed.last_seen > first - 100

    async def test_a_heartbeat_keeps_the_original_registration_time(self, registry_and_redis):
        """The portal shows uptime, which a heartbeat must not reset."""
        registry, _ = registry_and_redis
        registered = await registry.register(_backend())
        beat = await registry.heartbeat('gpu-burst-01')
        assert beat.registered_at == registered.registered_at

    async def test_a_heartbeat_for_an_expired_node_does_not_resurrect_it(self, registry_and_redis):
        """A heartbeat carries only an id. Reviving the node from that
        alone would put it back in rotation with no address and no
        capacity, so the caller is told to register again instead."""
        registry, redis = registry_and_redis
        await registry.register(_backend())
        redis.expire_key('gpu-burst-01')
        assert await registry.heartbeat('gpu-burst-01') is None
        assert await registry.list_nodes() == []


class TestDeregistration:
    async def test_deregistering_removes_the_node(self, registry_and_redis):
        registry, _ = registry_and_redis
        await registry.register(_backend())
        assert await registry.deregister('gpu-burst-01') is True
        assert await registry.list_nodes() == []

    async def test_deregistering_something_absent_is_not_an_error(self, registry_and_redis):
        """A stop-cluster job retrying after a timeout must not fail the
        second time — the goal held either way."""
        registry, _ = registry_and_redis
        assert await registry.deregister('never-existed') is False


class TestExpiry:
    async def test_an_expired_node_leaves_the_rotation(self, registry_and_redis):
        """The case a graceful shutdown never gets to handle: a reclaimed
        spot instance, a panicked kernel, a cut network."""
        registry, redis = registry_and_redis
        await registry.register(_backend('gpu-01'))
        await registry.register(_backend('gpu-02'))
        redis.expire_key('gpu-01')
        assert [n.backend.id for n in await registry.list_nodes()] == ['gpu-02']

    async def test_a_key_expiring_mid_scan_is_not_an_error(self, registry_and_redis):
        """It is the TTL doing its job a moment early, not a fault."""
        registry, redis = registry_and_redis
        await registry.register(_backend())
        redis.values['igni:server:nodes:registered:ghost'] = None  # type: ignore[assignment]
        assert [n.backend.id for n in await registry.list_nodes()] == ['gpu-burst-01']

    async def test_an_unreadable_entry_costs_one_node_not_the_list(self, registry_and_redis):
        """A payload written by an older shape would otherwise raise on
        every read and take the whole cluster down with it."""
        registry, redis = registry_and_redis
        await registry.register(_backend('gpu-01'))
        redis.values['igni:server:nodes:registered:gpu-02'] = '{"not": "a node"}'
        assert [n.backend.id for n in await registry.list_nodes()] == ['gpu-01']


class TestMergingWithStaticConfig:
    """How the two sources combine, and which wins."""

    async def test_static_and_registered_nodes_both_route(self, tmp_path, registry_and_redis):
        registry, _ = registry_and_redis
        await registry.register(_backend('gpu-burst'))

        path = tmp_path / 'backends.json'
        path.write_text(json.dumps([_backend('gpu-fixed').model_dump()]))
        provider = InferenceBackendProvider(path=str(path), registry=registry)
        assert sorted(b.id for b in await provider.load()) == ['gpu-burst', 'gpu-fixed']

    async def test_a_static_id_is_not_shadowed_by_a_registration(self, tmp_path, registry_and_redis):
        """The static entry is the operator's deliberate choice. A
        registration quietly overriding it would surface later as a
        routing mystery."""
        registry, _ = registry_and_redis
        await registry.register(_backend('gpu-01', base_url='http://impostor:8000/v1'))

        path = tmp_path / 'backends.json'
        path.write_text(json.dumps([_backend('gpu-01').model_dump()]))
        provider = InferenceBackendProvider(path=str(path), registry=registry)

        backends = await provider.load()
        assert len(backends) == 1
        assert backends[0].base_url == 'http://gpu-01.internal:8000/v1'

    async def test_a_registry_outage_degrades_to_the_static_nodes(self, tmp_path):
        """Redis being unreachable should cost the customer their burst
        capacity, not their inference."""

        class BrokenRegistry:
            async def list_nodes(self):
                raise ConnectionError('redis is down')

        path = tmp_path / 'backends.json'
        path.write_text(json.dumps([_backend('gpu-fixed').model_dump()]))
        provider = InferenceBackendProvider(path=str(path), registry=BrokenRegistry())
        assert [b.id for b in await provider.load()] == ['gpu-fixed']

    async def test_a_registry_outage_with_no_static_nodes_still_raises(self, tmp_path):
        """Degrading to nothing is not degrading gracefully, and the
        caller needs a config error rather than an empty list it would
        misread as "no node serves this pool"."""

        class BrokenRegistry:
            async def list_nodes(self):
                raise ConnectionError('redis is down')

        provider = InferenceBackendProvider(path='', inline='', registry=BrokenRegistry())
        with pytest.raises(BackendConfigError):
            await provider.load()

    async def test_static_ids_are_readable_without_touching_the_registry(self, tmp_path):
        """The registration endpoint asks this on every call, and it must
        not depend on Redis being up to reject a colliding id."""
        path = tmp_path / 'backends.json'
        path.write_text(json.dumps([_backend('gpu-01').model_dump(), _backend('gpu-02').model_dump()]))
        provider = InferenceBackendProvider(path=str(path), registry=None)
        assert provider.static_backend_ids() == {'gpu-01', 'gpu-02'}


class TestRoutingMap:
    def test_capacity_changes_do_not_count_as_repointing(self):
        """Raising a node's concurrency limit must not throw away its warm
        caches."""
        before = InferenceBackendProvider.routing_map([_backend('gpu-01', max_concurrent=8)])
        after = InferenceBackendProvider.routing_map([_backend('gpu-01', max_concurrent=64)])
        assert before == after

    def test_moving_a_node_does(self):
        before = InferenceBackendProvider.routing_map([_backend('gpu-01')])
        after = InferenceBackendProvider.routing_map([_backend('gpu-01', base_url='http://moved:8000/v1')])
        assert before != after

    def test_so_does_changing_its_model(self):
        """Different weights on the same machine is a cold cache too."""
        before = InferenceBackendProvider.routing_map([_backend('gpu-01', model='small')])
        after = InferenceBackendProvider.routing_map([_backend('gpu-01', model='large')])
        assert before != after


class TestSerialisation:
    def test_a_node_round_trips_through_json(self, registry_and_redis):
        """It is stored as JSON in Redis, so a field that does not survive
        the trip is a field the portal never sees."""
        node = RegisteredNode(backend=_backend(), registered_at=1.0, last_seen=2.0)
        assert RegisteredNode.model_validate_json(node.model_dump_json()) == node
