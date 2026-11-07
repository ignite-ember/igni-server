"""The inference backend pool.

The behaviours here are the reason the pool exists, and each of them
fails silently rather than loudly if it regresses — a slow deployment,
not a broken one:

* sticky routing, because a node's KV cache is the largest latency lever
  on a multi-GPU install
* capacity as a *live gauge*, because a rolling window would call an idle
  node full for having been busy an hour ago
* leases that self-heal, because a SIGKILLed worker would otherwise walk
  its node out of rotation permanently
* least-loaded by ratio, so a mixed cluster of 8-slot and 64-slot nodes
  balances by headroom rather than by count
"""

import json

import pytest

from app.services.ai.backend_pool import InferenceBackendPool
from app.services.ai.backend_provider import BackendConfigError, InferenceBackendProvider
from app.services.ai.schemas import (
    InferenceBackend,
    ModelNotAvailableError,
    NoBackendAvailableError,
    RegisteredNode,
)


class FakeRedis:
    """Enough Redis for the pool: strings, sorted sets, hashes, scan."""

    def __init__(self):
        self._strings: dict[str, str] = {}
        self._zsets: dict[str, dict[str, float]] = {}
        self._hashes: dict[str, dict[str, str]] = {}

    async def hgetall(self, key):
        return dict(self._hashes.get(key, {}))

    async def hset(self, key, mapping):
        self._hashes.setdefault(key, {}).update(mapping)

    async def hdel(self, key, *fields):
        for field in fields:
            self._hashes.get(key, {}).pop(field, None)

    async def get(self, key):
        return self._strings.get(key)

    async def set(self, key, value, ex=None):
        self._strings[key] = value

    async def delete(self, key):
        self._strings.pop(key, None)
        self._zsets.pop(key, None)
        self._hashes.pop(key, None)

    async def zadd(self, key, mapping):
        self._zsets.setdefault(key, {}).update(mapping)

    async def zrem(self, key, member):
        self._zsets.get(key, {}).pop(member, None)

    async def zcard(self, key):
        return len(self._zsets.get(key, {}))

    async def zremrangebyscore(self, key, min_score, max_score):
        zset = self._zsets.get(key, {})
        for member in [m for m, score in zset.items() if score <= max_score]:
            del zset[member]

    async def expire(self, key, ttl):
        pass

    async def scan_iter(self, match=None, count=None):
        prefix = (match or '').replace('*', '')
        for key in list(self._strings.keys()):
            if key.startswith(prefix):
                yield key

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis):
        self._redis = redis
        self._ops = []

    def zadd(self, key, mapping):
        self._ops.append(('zadd', key, mapping))
        return self

    def expire(self, key, ttl):
        return self

    def hset(self, key, mapping):
        self._ops.append(('hset', key, mapping))
        return self

    def hdel(self, key, *fields):
        self._ops.append(('hdel', key, fields))
        return self

    async def execute(self):
        for op in self._ops:
            if op[0] == 'zadd':
                await self._redis.zadd(op[1], op[2])
            elif op[0] == 'hset':
                await self._redis.hset(op[1], mapping=op[2])
            elif op[0] == 'hdel':
                await self._redis.hdel(op[1], *op[2])
        self._ops.clear()


class FakeRegistry:
    """A registry holding whatever the test put in it.

    Defaults to empty so the static-config tests describe only the file,
    and so nothing in this module depends on a live Redis.
    """

    def __init__(self, nodes=()):
        self.nodes = list(nodes)

    async def list_nodes(self):
        return list(self.nodes)


def _registered(backend: InferenceBackend) -> RegisteredNode:
    return RegisteredNode(backend=backend, registered_at=0.0, last_seen=0.0)


def _repoint(pool, tmp_path, backends) -> None:
    """Swap the pool's static config for a new set of backends."""
    path = tmp_path / f'repointed-{id(backends)}.json'
    path.write_text(json.dumps([b.model_dump() for b in backends]))
    pool._provider = InferenceBackendProvider(path=str(path), registry=FakeRegistry())


def _backend(backend_id: str, *, model='igni-1.0-27b', max_concurrent=2) -> InferenceBackend:
    return InferenceBackend(
        id=backend_id,
        base_url=f'http://{backend_id}.internal:8000/v1',
        model=model,
        max_concurrent=max_concurrent,
    )


@pytest.fixture
def pool_and_redis(tmp_path):
    def build(backends, **kwargs):
        path = tmp_path / f'backends-{len(backends)}-{id(backends)}.json'
        path.write_text(json.dumps([b.model_dump() for b in backends]))
        provider = InferenceBackendProvider(path=str(path), registry=FakeRegistry())
        pool = InferenceBackendPool(provider=provider, **kwargs)
        redis = FakeRedis()

        async def _stub_redis():
            return redis

        pool._get_redis = _stub_redis  # type: ignore[method-assign]
        return pool, redis

    return build


class TestConfiguration:
    async def test_no_backend_is_a_config_error_not_a_load_error(self, pool_and_redis):
        """Retrying cannot conjure a node, so the two must not share a class."""
        pool, _ = pool_and_redis([])
        with pytest.raises(BackendConfigError, match='No inference backends'):
            await pool.acquire('alice')

    async def test_an_unknown_model_lists_what_is_available(self, pool_and_redis):
        pool, _ = pool_and_redis([_backend('gpu-01', model='igni-1.0-27b')])
        with pytest.raises(ModelNotAvailableError) as exc:
            await pool.acquire('alice', model='gpt-4o')
        assert 'igni-1.0-27b' in str(exc.value)

    def test_a_backend_needs_an_absolute_url(self):
        """A bare host is the most likely config typo and fails loudly."""
        with pytest.raises(ValueError, match='http'):
            InferenceBackend(id='gpu-01', base_url='gpu-01.internal:8000', model='m')

    def test_a_token_is_optional(self):
        """A vLLM node on a private network usually has no auth at all."""
        assert _backend('gpu-01').api_key is None

    def test_duplicate_ids_are_rejected(self, tmp_path):
        """Ids namespace Redis keys, so duplicates would share a gauge and
        each node would look half as loaded as it really is."""
        path = tmp_path / 'dupes.json'
        path.write_text(json.dumps([_backend('gpu-01').model_dump(), _backend('gpu-01').model_dump()]))
        with pytest.raises(BackendConfigError, match='Duplicate'):
            InferenceBackendProvider(path=str(path))._read_static()

    def test_a_missing_file_says_which_path(self, tmp_path):
        with pytest.raises(BackendConfigError, match='does not exist'):
            InferenceBackendProvider(path=str(tmp_path / 'nope.json'))._read_static()

    async def test_nothing_configured_and_nothing_registered_explains_every_option(self):
        """Static config is no longer required — the registry alone is a
        valid deployment — so this fires only when both are empty, and it
        has to name all three ways out."""
        provider = InferenceBackendProvider(path='', inline='', registry=FakeRegistry())
        with pytest.raises(BackendConfigError, match='inference_backends_path'):
            await provider.load()

    async def test_registered_nodes_alone_are_enough(self):
        """A deployment whose GPUs all come and go configures no file at
        all, and must still route."""
        provider = InferenceBackendProvider(
            path='', inline='', registry=FakeRegistry([_registered(_backend('gpu-burst'))])
        )
        assert [b.id for b in await provider.load()] == ['gpu-burst']


class TestStickyRouting:
    async def test_the_same_user_returns_to_the_same_node(self, pool_and_redis):
        """The KV cache on that node is why this matters."""
        pool, _ = pool_and_redis([_backend('gpu-01'), _backend('gpu-02')])
        first = await pool.acquire('alice')
        await pool.release(first)
        second = await pool.acquire('alice')
        assert second.backend_id == first.backend_id

    async def test_a_different_model_gets_a_fresh_assignment(self, pool_and_redis):
        """The pinned node holds a different model's weights and is no help."""
        pool, _ = pool_and_redis([_backend('gpu-01', model='small'), _backend('gpu-02', model='large')])
        small = await pool.acquire('alice', model='small')
        large = await pool.acquire('alice', model='large')
        assert small.backend_id != large.backend_id

    async def test_a_saturated_pinned_node_falls_through(self, pool_and_redis):
        """A warm cache is worth a lot, but not worth queueing on a full GPU."""
        pool, _ = pool_and_redis([_backend('gpu-01', max_concurrent=1), _backend('gpu-02', max_concurrent=1)])
        pinned = await pool.acquire('alice')
        # Hold it, then ask again as the same user.
        second = await pool.acquire('alice')
        assert second.backend_id != pinned.backend_id

    async def test_repointing_a_node_flushes_its_pins(self, pool_and_redis, tmp_path):
        """Same id, different machine: the pin now promises a warm cache
        that is not there."""
        pool, redis = pool_and_redis([_backend('gpu-01'), _backend('gpu-02')])
        lease = await pool.acquire('alice')
        pinned = [k for k, v in redis._strings.items() if ':sticky:' in k and v == lease.backend_id]
        assert pinned

        moved = _backend(lease.backend_id).model_copy(update={'base_url': 'http://elsewhere:8000/v1'})
        other = next(b for b in (_backend('gpu-01'), _backend('gpu-02')) if b.id != lease.backend_id)
        _repoint(pool, tmp_path, [moved, other])

        await pool.acquire('bob')
        assert not [k for k in redis._strings if k in pinned]

    async def test_adding_a_node_keeps_every_pin(self, pool_and_redis, tmp_path):
        """The regression that matters once clusters autoscale.

        Flushing on any change would discard every warm KV cache in the
        deployment each time a node came up — turning the pool's largest
        performance lever into a liability precisely when load is high
        enough to be adding hardware.
        """
        pool, redis = pool_and_redis([_backend('gpu-01')])
        await pool.acquire('alice')
        before = dict((k, v) for k, v in redis._strings.items() if ':sticky:' in k)
        assert before

        _repoint(pool, tmp_path, [_backend('gpu-01'), _backend('gpu-02'), _backend('gpu-03')])
        await pool.acquire('bob')
        assert all(redis._strings.get(k) == v for k, v in before.items())

    async def test_a_departed_node_needs_no_flush(self, pool_and_redis, tmp_path):
        """The pin is left pointing at nothing, and the pick falls through
        to least-loaded on its own — so a scale-down costs nobody else
        their cache."""
        pool, _ = pool_and_redis([_backend('gpu-01'), _backend('gpu-02')])
        lease = await pool.acquire('alice')
        survivor = 'gpu-02' if lease.backend_id == 'gpu-01' else 'gpu-01'

        _repoint(pool, tmp_path, [_backend(survivor)])
        again = await pool.acquire('alice')
        assert again.backend_id == survivor


class TestCapacity:
    async def test_capacity_is_concurrency_not_history(self, pool_and_redis):
        """The point of the rewrite: a node that served many requests and
        released them all is idle, and must be treated as idle."""
        pool, _ = pool_and_redis([_backend('gpu-01', max_concurrent=1)])
        for _ in range(50):
            lease = await pool.acquire('alice')
            await pool.release(lease)
        # Still acquirable — nothing is in flight.
        assert await pool.acquire('alice')

    async def test_a_full_cluster_raises_a_retryable_error(self, pool_and_redis):
        pool, _ = pool_and_redis([_backend('gpu-01', max_concurrent=1)])
        await pool.acquire('alice')
        with pytest.raises(NoBackendAvailableError):
            await pool.acquire('bob')

    async def test_releasing_frees_the_slot(self, pool_and_redis):
        pool, _ = pool_and_redis([_backend('gpu-01', max_concurrent=1)])
        held = await pool.acquire('alice')
        await pool.release(held)
        assert await pool.acquire('bob')

    async def test_release_is_idempotent(self, pool_and_redis):
        """A double release must not free somebody else's slot."""
        pool, _ = pool_and_redis([_backend('gpu-01', max_concurrent=2)])
        first = await pool.acquire('alice')
        second = await pool.acquire('bob')
        await pool.release(first)
        await pool.release(first)
        # bob's lease still holds its slot, so only one is free.
        await pool.acquire('carol')
        with pytest.raises(NoBackendAvailableError):
            await pool.acquire('dave')
        await pool.release(second)

    async def test_an_abandoned_lease_expires(self, pool_and_redis):
        """A SIGKILLed worker must not permanently inflate a node's load."""
        pool, redis = pool_and_redis([_backend('gpu-01', max_concurrent=1)], lease_timeout=0)
        await pool.acquire('alice')
        # With a zero timeout every lease is already stale, so the next
        # acquire prunes it rather than reporting the node full.
        assert await pool.acquire('bob')


class TestLeastLoaded:
    async def test_balances_by_headroom_not_by_count(self, pool_and_redis):
        """An 8-slot node with 4 in flight is as loaded as a 64-slot node
        with 32, and a mixed cluster must see that."""
        pool, _ = pool_and_redis([_backend('small', max_concurrent=2), _backend('large', max_concurrent=10)])
        # One on each: small is now 50% loaded, large 10%.
        await pool.acquire('alice')
        await pool.acquire('bob')
        # A third user should land on whichever has more headroom.
        third = await pool.acquire('carol')
        assert third.backend_id == 'large'


class TestRetryPath:
    async def test_excluding_a_node_avoids_it(self, pool_and_redis):
        pool, _ = pool_and_redis([_backend('gpu-01'), _backend('gpu-02')])
        first = await pool.acquire('alice')
        retry = await pool.acquire('alice', exclude_backend_id=first.backend_id)
        assert retry.backend_id != first.backend_id

    async def test_a_single_node_deployment_retries_the_same_node(self, pool_and_redis):
        """Trying the only node again beats failing outright."""
        pool, _ = pool_and_redis([_backend('gpu-01', max_concurrent=4)])
        first = await pool.acquire('alice')
        retry = await pool.acquire('alice', exclude_backend_id=first.backend_id)
        assert retry.backend_id == 'gpu-01'

    async def test_a_retry_does_not_repin_the_user(self, pool_and_redis):
        """The excluded node just failed; the pin should survive for when
        it comes back rather than being moved by one bad request."""
        pool, redis = pool_and_redis([_backend('gpu-01'), _backend('gpu-02')])
        first = await pool.acquire('alice')
        pinned_before = await redis.get(pool._sticky_key(user_id='alice', model=None))
        await pool.acquire('alice', exclude_backend_id=first.backend_id)
        assert await redis.get(pool._sticky_key(user_id='alice', model=None)) == pinned_before


class TestLeaseContextManager:
    async def test_the_slot_is_returned_on_the_way_out(self, pool_and_redis):
        pool, _ = pool_and_redis([_backend('gpu-01', max_concurrent=1)])
        async with pool.lease('alice'):
            pass
        assert await pool.acquire('bob')

    async def test_the_slot_is_returned_even_when_the_body_raises(self, pool_and_redis):
        """A failing upstream call must not leak the node's slot."""
        pool, _ = pool_and_redis([_backend('gpu-01', max_concurrent=1)])
        with pytest.raises(RuntimeError):
            async with pool.lease('alice'):
                raise RuntimeError('upstream exploded')
        assert await pool.acquire('bob')


class TestModelAdvertising:
    async def test_distinct_model_and_url_pairs(self, pool_and_redis):
        pool, _ = pool_and_redis(
            [
                _backend('gpu-01', model='igni-1.0-27b'),
                _backend('gpu-02', model='igni-1.0-27b'),
                _backend('gpu-03', model='igni-1.0-8b'),
            ]
        )
        pairs = await pool.list_available_models()
        # Three nodes, three URLs, but only two models.
        assert len({model for model, _ in pairs}) == 2
        assert len(pairs) == 3
