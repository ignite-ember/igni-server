"""The inference pool's Redis and node registry, faked, and a pool built over a backend list.

Shared by every test of the pool, of the lease it hands out and of what
uses one, so none of them imports another test module for its fakes.
"""

from __future__ import annotations

import json
import pathlib

from app.services.ai.backend import InferenceBackend
from app.services.ai.backend_pool import InferenceBackendPool
from app.services.ai.backend_provider import InferenceBackendProvider


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


def pool_over(
    backends: list[InferenceBackend], tmp_path: pathlib.Path, **options
) -> tuple[InferenceBackendPool, FakeRedis]:
    """A real pool over *backends* as its static list, on a fake Redis it hands back too."""
    path = tmp_path / f'backends-{len(backends)}-{id(backends)}.json'
    path.write_text(json.dumps([backend.model_dump(mode='json') for backend in backends]))
    pool = InferenceBackendPool(provider=InferenceBackendProvider(path=str(path), registry=FakeRegistry()), **options)
    redis = FakeRedis()

    async def _redis() -> FakeRedis:
        return redis

    pool._get_redis = _redis  # type: ignore[method-assign]
    return pool, redis
