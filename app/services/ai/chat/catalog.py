"""The model identifiers this deployment serves, as `/chat/models` lists them."""

from __future__ import annotations

from cachetools import TTLCache

from app.api.base_v1.schema.chat import ChatModelEntry
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
        """
        if _KEY not in self._cache:
            served = await self._pools.get_pool().models_by_wire()
            self._cache[_KEY] = [ChatModelEntry(id=model_id, api=wire) for model_id, wire in served.items()]
        return list(self._cache[_KEY])

    def clear(self) -> None:
        """Forget the cached listing."""
        self._cache.clear()
