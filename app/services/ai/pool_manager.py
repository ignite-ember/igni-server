"""Owns the inference backend pool: the customer's nodes serving chat.

One pool over the configured and registered nodes. CodeIndex runs its own
nodes from its own configuration; igni server routes chat and nothing else.

The per-organization override pools that used to live here are gone with
the hosted deployment. One customer, one deployment, one set of GPUs —
there is nobody to override *for*.
"""

from __future__ import annotations

import logging

from app.services.ai.backend_pool import InferenceBackendPool
from app.services.ai.backend_provider import InferenceBackendProvider
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)


class PoolManager(metaclass=SingletonMeta):
    """Singleton holder for the pool.

    Singleton so the per-loop Redis clients and the provider's reload
    cache are shared for the life of the process rather than rebuilt per
    request.
    """

    def __init__(self, *, provider: InferenceBackendProvider | None = None) -> None:
        self._provider = provider or InferenceBackendProvider()
        self._pool: InferenceBackendPool | None = None

    @property
    def provider(self) -> InferenceBackendProvider:
        """The backend provider.

        Exposed so the registration endpoint can ask which ids are
        statically pinned without constructing a second provider and
        re-reading the config file to find out.
        """
        return self._provider

    def get_pool(self) -> InferenceBackendPool:
        """The pool, built on first use."""
        if self._pool is None:
            self._pool = InferenceBackendPool(provider=self._provider)
        return self._pool

    async def close(self) -> None:
        """Close the pool's Redis client. Called on shutdown."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
