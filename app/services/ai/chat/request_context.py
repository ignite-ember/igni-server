"""One chat request's caller, its upstream body, and the node lease serving it."""

from __future__ import annotations

import logging

from app.api.base_v1.schema.chat import CompletionUsage
from app.db.sql.chat_usage import ChatUsageDB
from app.services.ai.backend_pool import InferenceBackendPool
from app.services.ai.chat.errors import NoOtherNode
from app.services.ai.chat.schemas import ChatCaller, UpstreamChatRequest
from app.services.ai.fair_queue import FairQueue
from app.services.ai.schemas import BackendLease

logger = logging.getLogger(__name__)


class ChatRequestContext:
    """Owns the lease for the whole request, including a stream's tail.

    Holding the lease rather than a backend id is what keeps a node's
    concurrency gauge right when one caller has two requests in flight.
    """

    def __init__(
        self,
        *,
        caller: ChatCaller,
        request: UpstreamChatRequest,
        lease: BackendLease,
        pool: InferenceBackendPool,
        usage_db: ChatUsageDB,
        queue: FairQueue,
    ) -> None:
        self._caller = caller
        self._request = request
        self._lease = lease
        self._pool = pool
        self._usage_db = usage_db
        self._queue = queue

    @property
    def caller(self) -> ChatCaller:
        return self._caller

    @property
    def request(self) -> UpstreamChatRequest:
        return self._request

    @property
    def lease(self) -> BackendLease:
        return self._lease

    @property
    def model(self) -> str:
        """The model sent upstream, which is the model billed."""
        return self._request.model

    @property
    def user_id(self) -> str:
        return self._caller.user_id

    @property
    def org_id(self) -> str | None:
        return self._caller.org_id

    @property
    def app_id(self) -> str | None:
        return self._caller.app_id

    def switch_to(self, lease: BackendLease) -> None:
        """Point this request at another node: the lease and the model sent move together."""
        self._lease = lease
        self._request = self._request.retarget(lease.model)

    async def fail_over(self, *, failed_backend_id: str) -> None:
        """Hand the failed node's slot back, then take another node's.

        Released first, or a retry loop inflates the failed node's gauge.
        Any failure to take another node is `NoOtherNode`: the caller
        reports the original failure, which says what went wrong.
        """
        try:
            await self._pool.release(self._lease)
            lease = await self._pool.acquire(self.user_id, exclude_backend_id=failed_backend_id, model=self.model)
        except Exception as exc:  # noqa: BLE001 — named boundary: nothing left to try
            raise NoOtherNode(str(exc)) from exc
        self.switch_to(lease)

    async def log_usage(self, usage: CompletionUsage, *, duration: float, stream: bool) -> None:
        """Persist usage. Never releases: this is reached only when there is usage to write."""
        try:
            await self._usage_db.log(
                user_id=self.user_id,
                org_id=self.org_id,
                app_id=self.app_id,
                model=self.model,
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                duration=round(duration, 3),
                stream=stream,
            )
        except Exception as exc:  # noqa: BLE001 — usage is not the response
            logger.warning('Failed to log chat usage: %s', exc)

    async def release(self) -> None:
        """Hand the node's slot back, however this ended. Never masks the request's outcome.

        Idempotent: the pool removes a member from a Redis sorted set, and
        a failover may already have returned this lease. A missed release
        heals only at the pool's lease timeout, so it is logged loudly.
        """
        try:
            await self._pool.release(self._lease)
        except Exception as exc:  # noqa: BLE001 — never mask the response
            logger.warning('Failed to release inference backend: %s', exc)
        # A slot came free: the next in turn takes it.
        self._queue.kick()
