"""Which models the chat pool answers to, for refusing a name nothing serves."""

from __future__ import annotations

import logging

from redis.exceptions import RedisError

from app.services.ai.backend_provider import BackendConfigError
from app.services.ai.pool_manager import PoolManager
from app.services.portal.group_errors import ModelNotServed

logger = logging.getLogger(__name__)

#: What reading the pool's model list can fail with when the pool itself
#: is unreachable or unconfigured: the provider refusing its config
#: (`BackendConfigError`, which includes "nothing configured yet"), the
#: config file or a socket failing (`OSError`, `ConnectionError` among
#: them), and Redis. The registry, pinned-node and drain reads already
#: degrade on their own.
_POOL_UNREACHABLE = (BackendConfigError, OSError, RedisError)


class ServedModels:
    """Checks a model name against what the inference pool serves."""

    def __init__(self, *, pools: PoolManager | None = None) -> None:
        self._pools = pools or PoolManager()

    async def refuse_unserved(self, model: str | None) -> None:
        """Refuse a model no node in the chat pool answers to.

        Caught when the group or agent is saved rather than at request
        time, because the failure is otherwise invisible until somebody
        in that group tries to use it — and it lands on them, not on the
        admin who made the typo. Adapter names are free text passed to
        ``--lora-modules``, so a typo is the likely mistake rather than
        an exotic one.

        Group administration must not require the GPUs to be up: when the
        pool cannot be read, the check is skipped with a warning.
        """
        if not model:
            return

        try:
            pairs = await self._pools.get_pool().list_available_models()
        except _POOL_UNREACHABLE as exc:
            logger.warning('Could not check model %r against the pool: %s', model, exc)
            return

        served = {name for name, _ in pairs}
        if model not in served:
            raise ModelNotServed(model, served)
