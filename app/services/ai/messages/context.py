"""One admitted Messages request: the caller, the body, and the lease on the backend serving it."""

from __future__ import annotations

import logging
from typing import Any

from redis.exceptions import RedisError

from app.db.sql.chat_usage import ChatUsageDB
from app.services.ai.backend import BackendLease
from app.services.ai.backend_provider import BackendConfigError
from app.services.ai.chat.admission import ChatAdmission
from app.services.ai.chat.errors import NoOtherNode
from app.services.ai.chat.schemas import ChatCaller
from app.services.ai.fair_queue import FairQueue
from app.services.ai.messages.calls import CallerHeaders
from app.services.ai.messages.usage import MessagesUsage
from app.services.ai.pool_manager import PoolManager
from app.services.ai.providers.kinds import Wire
from app.services.ai.schemas import (
    AllBackendsDrainedError,
    ModelNotAvailableError,
    NoBackendAvailableError,
    NoBackendForWireError,
)

logger = logging.getLogger(__name__)

#: What the pool raises when there is no other backend to take: each is "stop looking", not a bug.
_NOTHING_TO_TAKE = (
    NoBackendAvailableError,
    AllBackendsDrainedError,
    ModelNotAvailableError,
    NoBackendForWireError,
    BackendConfigError,
    RedisError,
)


class MessagesAdmission(ChatAdmission):
    """The chat queue's admission, for a backend that speaks Anthropic's API."""

    async def _acquire(self, user_id: str, model: str | None) -> BackendLease:
        return await self._pool.acquire(user_id, model=model, wire=Wire.ANTHROPIC)


class MessagesContext:
    """One admitted request: its caller, its body, and the lease serving it — released once, however it ends."""

    def __init__(
        self,
        *,
        caller: ChatCaller,
        body: dict[str, Any],
        asked: CallerHeaders,
        lease: BackendLease,
        pools: PoolManager,
        usage_db: ChatUsageDB,
        queue: FairQueue,
    ) -> None:
        self.caller = caller
        self.body = body
        self.asked = asked
        self.lease = lease
        self._pools = pools
        self._usage_db = usage_db
        self._queue = queue

    @property
    def model(self) -> str:
        """The model asked for, which is the model billed."""
        return str(self.body['model'])

    async def fail_over(self, *, failed_backend_id: str) -> None:
        """Hand the failed backend's slot back and take another's. `NoOtherNode` when there is none."""
        pool = self._pools.get_pool()
        await self.release()
        try:
            self.lease = await pool.acquire(
                self.caller.user_id, model=self.model, exclude_backend_id=failed_backend_id, wire=Wire.ANTHROPIC
            )
        except _NOTHING_TO_TAKE as exc:
            raise NoOtherNode(str(exc)) from exc

    async def log_usage(self, usage: MessagesUsage, *, duration: float, stream: bool) -> None:
        """Persist usage. A failure here never touches the response."""
        try:
            await self._usage_db.log(
                user_id=self.caller.user_id,
                org_id=self.caller.org_id,
                app_id=self.caller.app_id,
                model=self.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output,
                total_tokens=usage.total_tokens,
                duration=round(duration, 3),
                stream=stream,
                backend_id=self.lease.backend_id,
                kind=self.lease.kind.value,
                cache_read_tokens=usage.cache_read,
                cache_write_tokens=usage.cache_write,
            )
        except Exception as exc:  # noqa: BLE001 — usage is not the response
            logger.warning('Failed to log Messages usage: %s', exc)

    async def release(self) -> None:
        """Hand the backend's slot back. Never masks the request's outcome."""
        try:
            await self._pools.get_pool().release(self.lease)
        except Exception as exc:  # noqa: BLE001 — never mask the response
            logger.warning('Failed to release inference backend: %s', exc)
        self._queue.kick()
