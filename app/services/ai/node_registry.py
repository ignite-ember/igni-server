"""Nodes that register themselves, for clusters that come and go.

The static JSON list describes hardware somebody decided about once. That
is the right model for a rack that is always on, and the wrong one for
the way GPUs are actually bought: a customer spins up four H100s for a
Tuesday load test, or an autoscaler replaces a spot node at 3am, or a
team stops their cluster overnight to stop paying for it. Editing a
ConfigMap and waiting for a rollout is not a workflow anyone will run
twice.

So a node can announce itself. The customer's start-cluster job POSTs a
registration when the node is serving, and their stop-cluster job deletes
it — and the pool routes to it in between, without a restart and without
a config change.

## Why Redis, and why a TTL

The registry is **current state of ephemeral infrastructure**, not
history. A node that stopped answering is a node that is not there, and
that is exactly what an expiring key means. A registration lives for
``_REGISTRATION_TTL_SECONDS`` and the node refreshes it; miss enough
heartbeats and it drops out of rotation on its own.

That matters because the failure this has to survive is not a graceful
stop — it is a spot reclaim, a kernel panic, a severed network. None of
those get to run the deregister call. A Postgres row would sit there
afterwards asserting that a dead machine is available, and every request
routed to it would fail until a human noticed. An expiring key needs
nobody.

The cost is that a Redis restart empties the registry. Recovery is one
heartbeat interval, because every live node re-announces on its next
beat, and nodes in the static file are unaffected. That is a good trade
for never routing to a machine that is not there.

## Static nodes always win

An id present in the static config cannot be registered over. A node that
tried is rejected at registration time with a 409, where the automation
that made the mistake can see it, rather than silently shadowing the
operator's deliberate configuration and being discovered later as a
routing mystery.

## The API key is sealed

A node fronted by an auth proxy registers with its key, and the entry
lives in Redis for its heartbeat window. The key is not in the entry
(F151): ``backend.api_key`` is stored as ``null`` and the key itself,
encrypted under ``SECRET_ENCRYPTION_KEY``, beside it as
``sealed_api_key``. It is decrypted on read, where routing needs it. An
entry whose key will not decrypt is skipped with an error naming the
node, as a pinned node is (F150) — routing to it without its key would
only collect its 401s — and its next heartbeat is answered 404, so the
node registers again and is sealed under the current key.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import redis.asyncio as aioredis

from app.config import settings
from app.db.redis.keys import key as redis_key
from app.db.redis.sealed import seal, unseal
from app.services.ai.schemas import InferenceBackend, RegisteredNode
from app.services.secrets import SecretDecryptError
from app.utils.redis_text import as_str

logger = logging.getLogger(__name__)

# How long a registration survives without a heartbeat. Three missed
# beats at the recommended 60s interval: long enough to ride out a GC
# pause or a blip, short enough that a dead node leaves the rotation
# before a developer notices the timeouts.
_REGISTRATION_TTL_SECONDS = 180

# What we tell nodes to use. Advertised in the register response so the
# interval lives in one place rather than in every customer's cron.
HEARTBEAT_INTERVAL_SECONDS = 60

_KEY_PREFIX = redis_key('nodes', 'registered') + ':'

# Where an entry keeps its API key, encrypted. Outside ``backend`` so the
# stored ``backend`` never carries a key at all.
_SEALED_API_KEY = 'sealed_api_key'


class NodeRegistry:
    """Self-registered inference nodes, held in Redis with a TTL."""

    def __init__(self) -> None:
        # Redis clients are loop-bound; one client per loop, or work on a
        # second loop trips "Future attached to a different loop". This
        # was routine when Celery ran each task in its own
        # ``asyncio.run``; DEC-13 removed that, and it stays keyed
        # because the tests run several loops and ``asyncio.run`` in a
        # script is a second one.
        self._redis_by_loop: dict[int, aioredis.Redis] = {}

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

    # ── Writes ───────────────────────────────────────────────────────

    async def register(self, backend: InferenceBackend) -> RegisteredNode:
        """Add or update a node and start its clock.

        Idempotent: re-registering an id replaces the entry rather than
        failing, because a node that restarts and re-announces is the
        normal case, not an error. The replacement is how a node moves to
        a new address after being rescheduled.
        """
        node = RegisteredNode(backend=backend, registered_at=time.time(), last_seen=time.time())
        await self._write(node)
        logger.info(
            'Registered inference node %s at %s (models %s)',
            backend.id,
            backend.base_url,
            ','.join(backend.models),
        )
        return node

    async def heartbeat(self, node_id: str) -> RegisteredNode | None:
        """Refresh a node's TTL. None if it had already expired.

        Returning None rather than resurrecting the node is deliberate:
        the caller learns it was dropped and re-registers with its full
        description, which is the only way we get the address and
        capacity back after an expiry.
        """
        existing = await self.get(node_id)
        if existing is None:
            return None
        refreshed = existing.model_copy(update={'last_seen': time.time()})
        await self._write(refreshed)
        return refreshed

    async def deregister(self, node_id: str) -> bool:
        """Remove a node. True if it was there, False if it was not.

        Called by a stop-cluster job. Draining is not attempted: leases
        already in flight against the node keep their base_url and finish
        against a machine that is still up for the moment it takes to
        shut down, and anything that does fail is retried against another
        node by the caller.
        """
        redis_client = await self._get_redis()
        removed = bool(await redis_client.delete(f'{_KEY_PREFIX}{node_id}'))
        if removed:
            logger.info('Deregistered inference node %s', node_id)
        return removed

    # ── Reads ────────────────────────────────────────────────────────

    async def get(self, node_id: str) -> RegisteredNode | None:
        redis_client = await self._get_redis()
        raw = await redis_client.get(f'{_KEY_PREFIX}{node_id}')
        return _parse(as_str(raw), node_id=node_id)

    async def list_nodes(self) -> list[RegisteredNode]:
        """Every live registration.

        Expiry is Redis's job, so anything returned here is by definition
        within its heartbeat window. Sorted by id to keep the portal's
        node list from reshuffling between refreshes.
        """
        redis_client = await self._get_redis()
        keys = [key async for key in redis_client.scan_iter(match=f'{_KEY_PREFIX}*', count=500)]
        if not keys:
            return []
        values = await redis_client.mget(keys)
        nodes = [
            node
            for key, raw in zip(keys, values)
            # A key can expire between the scan and the mget, which is not
            # an error — it is the TTL doing its job a moment early.
            if (node := _parse(as_str(raw), node_id=as_str(key).removeprefix(_KEY_PREFIX))) is not None
        ]
        return sorted(nodes, key=lambda node: node.backend.id)

    # ── Internals ────────────────────────────────────────────────────

    async def _write(self, node: RegisteredNode) -> None:
        redis_client = await self._get_redis()
        await redis_client.set(
            f'{_KEY_PREFIX}{node.backend.id}',
            _serialise(node),
            ex=_REGISTRATION_TTL_SECONDS,
        )


def _serialise(node: RegisteredNode) -> str:
    """The entry as stored: the API key sealed, never in ``backend``."""
    payload = node.model_dump(mode='json')
    api_key, payload['backend']['api_key'] = payload['backend'].get('api_key'), None
    if api_key:
        payload[_SEALED_API_KEY] = seal(api_key)
    return json.dumps(payload)


def _parse(raw: str | None, *, node_id: str) -> RegisteredNode | None:
    """Decode a stored registration, discarding one we can no longer read.

    A payload written by an older version whose shape has since changed
    would otherwise raise on every read and take the whole node list down
    with it. Dropping the single unreadable entry costs one node until it
    next heartbeats, which it will.
    """
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
        sealed = payload.pop(_SEALED_API_KEY, None) if isinstance(payload, dict) else None
        node = RegisteredNode.model_validate(payload)
    except (ValueError, json.JSONDecodeError):
        logger.warning('Discarding unreadable registration for node %s', node_id)
        return None
    if not sealed:
        # No key, or an entry written before F151 with the key in
        # ``backend``: read as it is, and sealed by the next heartbeat's
        # rewrite.
        return node
    try:
        api_key = unseal(sealed)
    except SecretDecryptError:
        logger.error(
            'Skipping registered inference node %s: its API key will not decrypt with any configured '
            'SECRET_ENCRYPTION_KEY. It registers again after its next heartbeat.',
            node_id,
        )
        return None
    return node.model_copy(update={'backend': node.backend.model_copy(update={'api_key': api_key})})
