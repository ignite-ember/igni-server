"""The model identifiers this deployment serves, as `/chat/models` lists them."""

from __future__ import annotations

from cachetools import TTLCache

from app.api.base_v1.schema.chat import ChatModelEntry
from app.services.ai.backend_provider import BackendConfigError
from app.services.ai.pool_manager import PoolManager

_KEY = 'models'


class ChatModelCatalog:
    """The pool's distinct model ids, cached for *ttl* seconds."""

    def __init__(self, *, pools: PoolManager, ttl: float = 60) -> None:
        self._pools = pools
        self._cache: TTLCache[str, list[ChatModelEntry]] = TTLCache(maxsize=1, ttl=ttl)

    async def models(self) -> list[ChatModelEntry]:
        """Each model once, in the order the pool first names it. Not per-org: the pool is global.

        A model served by several nodes is listed once; every call goes
        through this proxy, so the caller needs only the ids.

        No node at all — none configured, or the last registered one
        stopped or expired, as a cluster stopped overnight does — is an
        empty list, not an error: clients discover models on start, and a
        500 there broke them whenever the GPUs were off. Not cached, so
        the first node back is listed on the next call.
        """
        if _KEY not in self._cache:
            try:
                served = await self._pools.get_pool().models_by_wire()
            except BackendConfigError:
                return []
            self._cache[_KEY] = [ChatModelEntry(id=model_id, api=wire) for model_id, wire in served.items()]
        return list(self._cache[_KEY])

    def clear(self) -> None:
        """Forget the cached listing."""
        self._cache.clear()
