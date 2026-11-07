"""Routing across the customer's inference nodes.

Three behaviours, in the order they matter.

**Sticky per user.** The same developer is routed back to the same node
for as long as the assignment holds. This is not fairness plumbing — it
is the largest performance lever here. A vLLM node keeps a prefix/KV
cache, and a developer's next turn shares almost all of its prefix with
the last one. Landing on a different node throws that away and re-decodes
the whole context. The previous incarnation of this class did the same
thing for the same reason, phrased as keeping a *provider's* prompt cache
warm; on the customer's own hardware the argument is stronger, because
the cache is theirs and they paid for the GPU holding it.

**Concurrency, not throughput.** A node's limit is how many sequences it
is decoding right now, so capacity is a gauge of in-flight leases rather
than a rolling window of past requests. A window would call a node full
because it served a thousand requests in the last hour while it sits idle
— exactly backwards for scheduling.

**Leases self-heal.** In-flight work is a Redis sorted set scored by
start time, and stale entries are pruned on read. A worker that is
SIGKILLed mid-request leaves its lease behind; without pruning that node
would look permanently one-busier, and after enough crashes it would drop
out of rotation entirely. Pruning means the damage expires.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from contextlib import asynccontextmanager
import logging
import time
from typing import Any, cast
import uuid

import redis.asyncio as aioredis

from app.config import settings
from app.db.redis.keys import key as redis_key
from app.services.ai.backend_provider import InferenceBackendProvider
from app.services.ai.health import combine
from app.services.ai.node_drain import NodeDrainRegistry
from app.services.ai.schemas import (
    AllBackendsDrainedError,
    BackendLease,
    BackendStatus,
    DEFAULT_LEASE_TIMEOUT_SECONDS,
    HealthState,
    InferenceBackend,
    ModelNotAvailableError,
    NoBackendAvailableError,
    NodeHealth,
    NodeMetrics,
    RegisteredNode,
)

logger = logging.getLogger(__name__)

# How long a user stays pinned to a node. Long enough to span a working
# session — the whole point is cache reuse across a conversation — and
# bounded so a node added to the cluster starts receiving traffic without
# waiting for everyone's session to end.
_STICKY_TTL = 6 * 3600


class InferenceBackendPool:
    """Stateless router over a set of inference backends; state is in Redis."""

    def __init__(
        self,
        *,
        provider: InferenceBackendProvider | None = None,
        drains: NodeDrainRegistry | None = None,
        lease_timeout: int = DEFAULT_LEASE_TIMEOUT_SECONDS,
    ) -> None:
        self.lease_timeout = lease_timeout
        self._provider = provider or InferenceBackendProvider()
        self._drains = drains or NodeDrainRegistry()
        # Redis clients are loop-bound. One client per loop, or work on a
        # second loop trips "Future attached to a different loop" — which
        # was routine when Celery ran each task in its own
        # ``asyncio.run``, and is still reachable from the tests and from
        # any script that calls ``asyncio.run``.
        self._redis_by_loop: dict[int, aioredis.Redis] = {}
        self._inflight_prefix = redis_key('backends', 'inflight') + ':'
        self._sticky_prefix = redis_key('backends', 'sticky') + ':'
        self._routing_key = redis_key('backends', 'routing')

    # ── Lifecycle ────────────────────────────────────────────────────

    async def _get_redis(self) -> aioredis.Redis:
        loop_id = id(asyncio.get_running_loop())
        client = self._redis_by_loop.get(loop_id)
        if client is None:
            client = aioredis.from_url(settings.redis_url, decode_responses=True)
            self._redis_by_loop[loop_id] = client
        return client

    async def close(self) -> None:
        try:
            loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            return
        client = self._redis_by_loop.pop(loop_id, None)
        if client is not None:
            await client.aclose()

    # ── Public API ───────────────────────────────────────────────────

    @property
    def drains(self) -> NodeDrainRegistry:
        """The drain registry this pool reads on every acquire.

        Exposed so whatever takes a node out of rotation writes through
        the same registry — and the same Redis client — that routing
        reads, rather than opening a second one.
        """
        return self._drains

    @asynccontextmanager
    async def lease(
        self,
        user_id: str,
        *,
        model: str | None = None,
        exclude_backend_id: str | None = None,
    ):
        """Hold a backend for the duration of one request.

        The context-manager form is the one to reach for. Every acquire
        must be paired with a release or the node's gauge drifts upward
        until it falls out of rotation, and a summarisation phase makes
        thousands of acquires — relying on the staleness sweep to clean
        up after each one would mean the gauge is wrong for as long as
        the lease timeout, which is exactly when scheduling matters.
        """
        held = await self.acquire(user_id, model=model, exclude_backend_id=exclude_backend_id)
        try:
            yield held
        finally:
            await self.release(held)

    async def acquire(
        self,
        user_id: str,
        *,
        model: str | None = None,
        exclude_backend_id: str | None = None,
    ) -> BackendLease:
        """Claim the least-loaded backend.

        Prefer :meth:`lease`. Use this directly only when the release
        point is genuinely elsewhere — a streaming response released when
        the stream closes, for instance.

        ``exclude_backend_id`` skips a node that just failed and bypasses
        stickiness, so a retry does not land back on the broken one.
        """
        redis_client = await self._get_redis()
        backends = await self._provider.load()
        await self._flush_sticky_if_config_changed(redis_client, backends)

        # An operator's decision, applied at selection rather than by
        # hiding the node: a drained node still appears in the portal, or
        # the switch could not be switched back. In-flight work is not
        # touched — a request holding a lease finishes on its node, which
        # is what makes this usable before maintenance.
        drained = await self._drains.drained_ids()
        # ``load`` has already refused an empty list (``BackendConfigError``),
        # so no candidates means every node was drained: distinguished from
        # "nothing is configured" because the fix is entirely different —
        # somebody turned these off and can turn them back on.
        candidates = [backend for backend in backends if backend.id not in drained]
        if not candidates:
            raise AllBackendsDrainedError([backend.id for backend in backends])

        if model is not None:
            # Membership, not equality: a LoRA node serves its base model
            # and every adapter, and any of those names may be asked for.
            matching = [backend for backend in candidates if backend.serves(model)]
            if not matching:
                available = [name for backend in candidates for name in backend.models]
                raise ModelNotAvailableError(model=model, available=available)
            candidates = matching

        if exclude_backend_id is not None:
            remaining = [backend for backend in candidates if backend.id != exclude_backend_id]
            # Fall back to the excluded node when it is the only one. A
            # single-node deployment retrying is better served by trying
            # the same node again than by failing outright.
            candidates = remaining or candidates
        else:
            sticky = await self._sticky_pick(redis_client, candidates, user_id=user_id, model=model)
            if sticky is not None:
                return await self._claim(redis_client, sticky, model=model)

        chosen = await self._least_loaded(redis_client, candidates)
        if chosen is None:
            raise NoBackendAvailableError()

        if exclude_backend_id is None:
            await redis_client.set(self._sticky_key(user_id=user_id, model=model), chosen.id, ex=_STICKY_TTL)
        return await self._claim(redis_client, chosen, model=model)

    async def release(self, lease: BackendLease) -> None:
        """Give a backend's slot back. Safe to call twice."""
        redis_client = await self._get_redis()
        await redis_client.zrem(f'{self._inflight_prefix}{lease.backend_id}', lease.lease_id)

    async def list_available_models(self) -> list[tuple[str, str]]:
        """Distinct ``(model, base_url)`` pairs.

        The CLI asks so it can advertise which model identifiers this
        deployment accepts.
        """
        backends = await self._provider.load()
        # Drained nodes are not advertised: offering a model that only a
        # drained node serves produces a request that cannot be routed.
        drained = await self._drains.drained_ids()
        pairs: dict[tuple[str, str], None] = {}
        for backend in backends:
            if backend.id not in drained:
                # Every id the node answers to, so an adapter is
                # advertised as its own selectable model.
                for name in backend.models:
                    pairs.setdefault((name, backend.base_url), None)
        return list(pairs.keys())

    async def backend_statuses(
        self,
        *,
        registered: dict[str, RegisteredNode],
        sources: dict[str, str],
        health: dict[str, NodeHealth],
        metrics: dict[str, NodeMetrics] | None = None,
    ) -> list[BackendStatus]:
        """Every backend, with its load and its health.

        The portal's node view. Everything cross-cutting is passed in
        rather than read here, so the caller decides how often to read the
        registry, look up sources and probe.
        """
        redis_client = await self._get_redis()
        backends = await self._provider.load()
        drains = await self._drains.all()
        now = time.time()

        statuses = []
        for backend in backends:
            node = registered.get(backend.id)
            in_flight = await self._in_flight(redis_client, backend)
            last_seen_ago = (now - node.last_seen) if node is not None else None
            probe = health.get(backend.id) or NodeHealth(state=HealthState.UNKNOWN)
            reported = (metrics or {}).get(backend.id)
            statuses.append(
                BackendStatus(
                    id=backend.id,
                    base_url=backend.base_url,
                    models=list(backend.models),
                    multimodal=backend.multimodal,
                    # A node in the registry is registered even if it is
                    # also named elsewhere; the source map holds the two
                    # that are not.
                    source=sources.get(backend.id, 'registered'),  # type: ignore[arg-type]
                    in_flight=in_flight,
                    max_concurrent=backend.max_concurrent,
                    health=combine(
                        probe,
                        in_flight=in_flight,
                        max_concurrent=backend.max_concurrent,
                        last_seen_seconds_ago=last_seen_ago,
                        metrics=reported,
                    ),
                    metrics=reported,
                    last_seen_seconds_ago=last_seen_ago,
                    registered_at=node.registered_at if node is not None else None,
                    drain=drains.get(backend.id),
                )
            )
        return statuses

    async def log_pool_summary(self) -> None:
        """Log the configured nodes and their capacity. Called at startup."""
        try:
            backends = await self._provider.load()
        except Exception as exc:  # noqa: BLE001 — startup logging must not break boot
            logger.warning('Could not load inference backends: %s', exc)
            return
        if not backends:
            logger.warning('No inference backends are configured')
            return
        capacity = sum(backend.max_concurrent for backend in backends)
        logger.info('Inference backends — %d node(s), %d concurrent', len(backends), capacity)

    # ── Internals ────────────────────────────────────────────────────

    def _sticky_key(self, *, user_id: str, model: str | None) -> str:
        # The model is part of the key so switching model yields a fresh
        # assignment: the old node's cache holds a different model's
        # weights and is no help.
        suffix = user_id if model is None else f'{model}:{user_id}'
        return f'{self._sticky_prefix}{suffix}'

    async def _sticky_pick(
        self,
        redis_client: aioredis.Redis,
        candidates: list[InferenceBackend],
        *,
        user_id: str,
        model: str | None,
    ) -> InferenceBackend | None:
        """The user's pinned backend, if it still exists and has room."""
        assigned = await redis_client.get(self._sticky_key(user_id=user_id, model=model))
        if not assigned:
            return None
        backend = next((b for b in candidates if b.id == assigned), None)
        if backend is None:
            return None
        if await self._in_flight(redis_client, backend) >= backend.max_concurrent:
            # Pinned node is full. Fall through to least-loaded rather
            # than queueing behind it — a warm cache is worth a lot, but
            # not worth waiting for a saturated GPU.
            return None
        return backend

    async def _in_flight(self, redis_client: aioredis.Redis, backend: InferenceBackend) -> int:
        """Current concurrency for a backend, pruning abandoned leases.

        The prune is what keeps a crashed worker from permanently
        inflating a node's load.
        """
        key = f'{self._inflight_prefix}{backend.id}'
        await redis_client.zremrangebyscore(key, '-inf', time.time() - self.lease_timeout)
        return int(await redis_client.zcard(key))

    async def _least_loaded(
        self, redis_client: aioredis.Redis, candidates: list[InferenceBackend]
    ) -> InferenceBackend | None:
        """The candidate with the most headroom, or None if all are full.

        Compared by *ratio* rather than absolute count so a mixed cluster
        balances sensibly — an 8-slot node with 4 in flight is as loaded
        as a 64-slot node with 32, and should be treated that way.
        """
        best: InferenceBackend | None = None
        best_ratio = 1.0
        for backend in candidates:
            ratio = await self._in_flight(redis_client, backend) / backend.max_concurrent
            if ratio < 1.0 and ratio < best_ratio:
                best, best_ratio = backend, ratio
        return best

    async def _claim(
        self, redis_client: aioredis.Redis, backend: InferenceBackend, *, model: str | None = None
    ) -> BackendLease:
        """Record an in-flight lease and return it.

        The lease carries the model the caller asked for, not the node's
        first one: on a LoRA node those differ, and sending the base
        model upstream would quietly serve every request from the wrong
        weights.
        """
        lease_id = uuid.uuid4().hex
        key = f'{self._inflight_prefix}{backend.id}'
        pipe = redis_client.pipeline()
        pipe.zadd(key, {lease_id: time.time()})
        # The TTL is a backstop for a node removed from the config while
        # leases are outstanding; pruning handles the normal case.
        pipe.expire(key, self.lease_timeout * 2)
        await pipe.execute()
        return BackendLease(
            lease_id=lease_id,
            backend_id=backend.id,
            base_url=backend.base_url,
            model=model or backend.primary_model,
            api_key=backend.api_key,
        )

    async def _flush_sticky_if_config_changed(
        self, redis_client: aioredis.Redis, backends: list[InferenceBackend]
    ) -> None:
        """Drop the pins that now point at different hardware — only those.

        A node *leaving* needs no flush: the sticky pick already checks
        that the assigned backend is still in the candidate list and
        falls through to least-loaded when it is not. A node *joining*
        needs no flush either; it simply becomes the least loaded and
        starts taking new assignments.

        What does need one is an id re-pointed at a different machine,
        because a user pinned to it would be sent somewhere cold while we
        still believe the cache is warm. Flushing only those keys is what
        makes this safe to run in a cluster that autoscales — the earlier
        flush-everything rule would have discarded every warm cache in
        the deployment each time a node came up.
        """
        current = self._provider.routing_map(backends)
        # redis-py types hgetall as possibly-sync depending on the client;
        # this one is always async.
        previous: dict[str, str] = await cast(Awaitable[dict[str, str]], redis_client.hgetall(self._routing_key))

        # In Redis rather than in memory because the comparison has to
        # hold across processes: every API replica loads the config
        # independently, and per-instance state would mean the first
        # process to notice a change flushed on everyone's behalf while
        # the rest never did.
        repointed = {
            backend_id
            for backend_id, routing in current.items()
            if backend_id in previous and previous[backend_id] != routing
        }

        if previous != current:
            pipe = redis_client.pipeline()
            if departed := set(previous) - set(current):
                pipe.hdel(self._routing_key, *departed)
            if current:
                # redis-py types the mapping key as an invariant union, which
                # no concrete dict satisfies.
                pipe.hset(self._routing_key, mapping=cast(Any, current))
            await pipe.execute()

        if not repointed:
            return

        logger.warning('Inference backends %s were re-pointed — flushing their sticky assignments', sorted(repointed))
        async for key in redis_client.scan_iter(match=f'{self._sticky_prefix}*', count=500):
            if await redis_client.get(key) in repointed:
                await redis_client.delete(key)
