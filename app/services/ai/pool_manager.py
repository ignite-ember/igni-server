"""Owns the chat and codeindex backend pools.

Two pools over one backend list. A node declares which it serves, and a
single-node install simply serves both.

The separation earns its keep under load: a 5,000-file indexing run will
happily saturate every GPU it can reach, and a developer waiting on a
completion behind it has a very different tolerance for latency than a
batch job does. Pointing them at different nodes is the operator's lever
for that, and it costs nothing when they do not use it.

The per-organization override pools that used to live here are gone with
the hosted deployment. One customer, one deployment, one set of GPUs —
there is nobody to override *for*.
"""

from __future__ import annotations

import logging

from app.services.ai.backend_pool import InferenceBackendPool
from app.services.ai.backend_provider import InferenceBackendProvider
from app.services.ai.schemas import PoolType
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)


class PoolManager(metaclass=SingletonMeta):
    """Singleton holder for the pools.

    Singleton so the per-loop Redis clients and the provider's reload
    cache are shared for the life of the process rather than rebuilt per
    request.
    """

    def __init__(self, *, provider: InferenceBackendProvider | None = None) -> None:
        # One provider behind both pools: they read the same backend list
        # and differ only in which entries they route to, so two would
        # mean two file reads and two chances to disagree.
        self._provider = provider or InferenceBackendProvider()
        # Keyed rather than one attribute per pool: a third pool meant
        # touching four methods, and `close_all_pools` naming them
        # individually is how one gets left open. `VALID_POOL_TYPES` is
        # the one place the set is written down.
        self._pools: dict[PoolType, InferenceBackendPool] = {}

    @property
    def provider(self) -> InferenceBackendProvider:
        """The shared backend provider.

        Exposed so the registration endpoint can ask which ids are
        statically pinned without constructing a second provider and
        re-reading the config file to find out.
        """
        return self._provider

    def get_pool(self, pool: PoolType) -> InferenceBackendPool:
        """The pool by name, built on first use."""
        if pool not in self._pools:
            self._pools[pool] = InferenceBackendPool(pool, provider=self._provider)
        return self._pools[pool]

    def get_chat_pool(self) -> InferenceBackendPool:
        return self.get_pool('chat')

    def get_codeindex_pool(self) -> InferenceBackendPool:
        return self.get_pool('codeindex')

    async def close_all_pools(self) -> None:
        """Close every pool's Redis client. Called on shutdown.

        Iterates what was actually built: a pool nobody asked for has
        no Redis client to close, and naming them individually is how
        a third one gets left open.
        """
        for pool in self._pools.values():
            await pool.close()
        self._pools.clear()
