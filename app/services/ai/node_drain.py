"""Taking a GPU out of rotation without taking it away.

A node could be removed and it could be edited, and neither is what an
operator wants at 3am. Removal is destructive and only works for nodes
added from the portal; editing needs a form, and a self-registered node
had no editable state at all — its "Serves" field read *"set where this
node is defined"*, because a registration is idempotent and the cluster
re-announces itself every 60 seconds. Anything the portal wrote would be
overwritten inside three minutes.

So: an override, held separately from the thing it overrides.

## Why it lives outside the registration

The registration is the *node's* statement about itself, with a TTL, and
the heartbeat keeps it alive. A drain is the *operator's* statement about
that node, and it has no TTL. Writing the drain into the registration
would mean a node restart silently resumes traffic to a GPU somebody
deliberately stopped — which is exactly the failure that made this
read-only in the first place, so storing it there would have reproduced
the problem it exists to solve.

The consequence to be deliberate about: a drained node that is removed
and never comes back leaves its key behind. That is the correct trade —
an orphan key costs nothing, and the alternative is expiring an
operator's decision on a schedule they did not choose. `resume` is the
only thing that clears it, and the portal shows every drain so an orphan
is visible rather than lurking.

## What it stops, and what it does not

Draining removes a node from *selection*: `acquire` skips it, and
`list_available_models` stops advertising models only it serves. It does
**not** cancel in-flight work. A request that already holds a lease keeps
its node and finishes; a streaming response keeps streaming. That is what
makes this usable before maintenance — drain, watch `in_flight` fall to
zero on the node's row, then do the work.

Nor does it hide the node. A drained node still appears in the portal,
marked, because a switch that makes the thing it switched off disappear
cannot be switched back.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging

from pydantic import BaseModel, Field
import redis.asyncio as aioredis

from app.config import settings
from app.db.redis.keys import key as redis_key

logger = logging.getLogger(__name__)

_KEY_PREFIX = redis_key('nodes', 'drained') + ':'


class NodeDrain(BaseModel):
    """An operator's decision that a node should not serve.

    Who and when are stored, not just the fact. A drained node found
    weeks later is a question — "why is this off?" — and an audit search
    is a poor way to answer it when the answer can travel with the state.
    """

    node_id: str
    by: str = Field(description='The user id of the administrator who drained it.')
    by_email: str | None = None
    at: datetime
    reason: str | None = None


class NodeDrainRegistry:
    """Which nodes an operator has taken out of rotation."""

    def __init__(self) -> None:
        # One client per loop, for the reason `NodeRegistry` documents:
        # Redis clients are loop-bound.
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

    async def drain(
        self, node_id: str, *, by: str, by_email: str | None = None, reason: str | None = None
    ) -> NodeDrain:
        """Stop routing to ``node_id``. No expiry — see the module docstring."""
        record = NodeDrain(
            node_id=node_id,
            by=by,
            by_email=by_email,
            at=datetime.now(timezone.utc),
            reason=reason,
        )
        redis_client = await self._get_redis()
        await redis_client.set(f'{_KEY_PREFIX}{node_id}', record.model_dump_json())
        logger.info('Node %s drained by %s%s', node_id, by, f' ({reason})' if reason else '')
        return record

    async def resume(self, node_id: str) -> bool:
        """Let ``node_id`` serve again. True when it was actually drained."""
        redis_client = await self._get_redis()
        resumed = bool(await redis_client.delete(f'{_KEY_PREFIX}{node_id}'))
        if resumed:
            logger.info('Node %s resumed', node_id)
        return resumed

    async def all(self) -> dict[str, NodeDrain]:
        """Every drain, by node id.

        One scan rather than a lookup per node: the routing path asks for
        this on every acquire, and it is the whole set that matters there
        rather than any one entry.
        """
        redis_client = await self._get_redis()
        keys = [key async for key in redis_client.scan_iter(match=f'{_KEY_PREFIX}*', count=500)]
        if not keys:
            return {}
        drains: dict[str, NodeDrain] = {}
        for key, raw in zip(keys, await redis_client.mget(keys)):
            node_id = str(key).removeprefix(_KEY_PREFIX)
            if raw is None:
                # Deleted between the scan and the mget: somebody resumed
                # it a moment ago, which is not an error.
                continue
            try:
                drains[node_id] = NodeDrain.model_validate(json.loads(raw))
            except Exception:  # noqa: BLE001 — a corrupt record must not stop routing
                # Draining is a *restriction*, so a record we cannot read
                # is kept rather than discarded: forgetting it would send
                # traffic to a node somebody deliberately stopped. The
                # `at` is a placeholder so the portal can still show the
                # state and offer Resume.
                logger.warning('Unreadable drain record for node %s; keeping it drained', node_id)
                drains[node_id] = NodeDrain(
                    node_id=node_id,
                    by='unknown',
                    at=datetime.fromtimestamp(0, tz=timezone.utc),
                    reason='the stored record could not be read',
                )
        return drains

    async def drained_ids(self) -> set[str]:
        """Just the ids, for the routing path.

        Fails **open** — an empty set — when Redis cannot be reached, and
        that is the uncomfortable choice of the two. Failing closed would
        mean a Redis blip drains the entire fleet and every chat request
        gets "no backend configured", turning a cache outage into a total
        one. Failing open means a drained node may briefly take traffic
        again during that blip. A GPU serving requests it should not is
        recoverable; a deployment that serves nothing is an incident.

        The same call already gates concurrency leases through Redis, so
        a deployment whose Redis is down is not routing much anyway.
        """
        try:
            redis_client = await self._get_redis()
            keys = [key async for key in redis_client.scan_iter(match=f'{_KEY_PREFIX}*', count=500)]
        except Exception as exc:  # noqa: BLE001 — must not be able to break routing
            logger.warning('Could not read node drains, routing as if none are set: %s', exc)
            return set()
        return {str(key).removeprefix(_KEY_PREFIX) for key in keys}
