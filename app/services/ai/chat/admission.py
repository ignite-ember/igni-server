"""Admission to a node: a lease now, or one in turn, or a refusal that says why."""

from __future__ import annotations

from app.services.ai.backend_pool import InferenceBackendPool
from app.services.ai.backend_provider import BackendConfigError
from app.services.ai.chat.errors import (
    BackendMisconfiguredRefusal,
    ModelUnavailableRefusal,
    QueueFullRefusal,
    QueueTimeoutRefusal,
)
from app.services.ai.chat.schemas import ChatCaller
from app.services.ai.fair_queue import FairQueue, QueueFull, QueueTimeout
from app.services.ai.schemas import BackendLease, ModelNotAvailableError


class ChatAdmission:
    """Every node full: wait in turn, fairly between organisations, then producers.

    A caller who leaves while waiting cancels the take, which leaves the
    queue; a slot granted as it gave up goes back to the pool.
    """

    def __init__(self, *, pool: InferenceBackendPool, queue: FairQueue, max_wait: float) -> None:
        self._pool = pool
        self._queue = queue
        self._max_wait = max_wait

    async def admit(self, *, caller: ChatCaller, model: str | None) -> BackendLease:
        """A lease on a node serving *model* (any model when None), or a `ChatRefused`."""
        try:
            return await self._queue.take(
                org=caller.org_id or '',
                producer=caller.user_id,
                user_id=caller.user_id,
                model=model,
                acquire=self._acquire,
                release=self._pool.release,
                max_wait=self._max_wait,
            )
        except ModelNotAvailableError as exc:
            raise ModelUnavailableRefusal(str(exc)) from exc
        except BackendConfigError as exc:
            raise BackendMisconfiguredRefusal(str(exc)) from exc
        except QueueFull as exc:
            raise QueueFullRefusal() from exc
        except QueueTimeout as exc:
            raise QueueTimeoutRefusal() from exc

    async def _acquire(self, user_id: str, model: str | None) -> BackendLease:
        return await self._pool.acquire(user_id, model=model)
