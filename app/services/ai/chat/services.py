"""The chat proxy's composition root: one per app, handed to the router as a dependency."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Request
from starlette.applications import Starlette

from app.config import settings
from app.db.sql.chat_usage import ChatUsageDB
from app.security.producer import Producers
from app.services.ai.chat.admission import ChatAdmission
from app.services.ai.chat.callers import ChatCallerResolver
from app.services.ai.chat.catalog import ChatModelCatalog
from app.services.ai.chat.completion import ChatCompletionService
from app.services.ai.chat.request_context import ChatRequestContext
from app.services.ai.chat.schemas import ChatCaller, UpstreamChatRequest
from app.services.ai.chat.streaming import ChatStreamer
from app.services.ai.chat.tails import TailRunner
from app.services.ai.chat.upstream_client import UpstreamChatClient
from app.services.ai.fair_queue import FairQueue
from app.services.ai.pool_manager import PoolManager
from app.services.ai.schemas import BackendLease
from app.services.ai.token_estimate import TokenEstimator
from app.services.ai.upstream import RetryPolicy
from app.services.auth.auth_service import AuthService
from app.services.portal.group_model_service import GroupModelService


def _loop_queue() -> FairQueue:
    """This event loop's queue, at the length configured now."""
    return FairQueue.of_loop(max_length=settings.chat_queue_max_length)


class ChatServices:
    """Builds the chat proxy's parts once and wires them together.

    Long-lived on purpose: the tail runner must outlive a request so a
    shielded tail is never collected, and the model catalog caches. The
    pool and the queue are looked up per request, so configuration read
    at request time still applies.
    """

    def __init__(
        self,
        *,
        pools: PoolManager | None = None,
        queue_factory: Callable[[], FairQueue] = _loop_queue,
        usage_db: ChatUsageDB | None = None,
        estimator: TokenEstimator | None = None,
        tails: TailRunner | None = None,
        upstream: UpstreamChatClient | None = None,
        retry: RetryPolicy | None = None,
        callers: ChatCallerResolver | None = None,
        catalog: ChatModelCatalog | None = None,
    ) -> None:
        self._pools = pools or PoolManager()
        self._queue_factory = queue_factory
        self._usage_db = usage_db or ChatUsageDB()
        self._tails = tails or TailRunner()
        self._callers = callers or ChatCallerResolver(
            producers=Producers(), auth=AuthService, group_models=GroupModelService()
        )
        self._catalog = catalog or ChatModelCatalog(pools=self._pools)
        upstream = upstream or UpstreamChatClient()
        estimator = estimator or TokenEstimator()
        retry = retry or RetryPolicy()
        self._completions = ChatCompletionService(
            upstream=upstream, estimator=estimator, tails=self._tails, retry=retry
        )
        self._streamer = ChatStreamer(upstream=upstream, estimator=estimator, tails=self._tails, retry=retry)

    @classmethod
    def of_app(cls, app: Starlette) -> ChatServices:
        """The app's instance, built on first use, so it works with or without a lifespan."""
        services: ChatServices | None = getattr(app.state, 'chat_services', None)
        if services is None:
            services = app.state.chat_services = cls()
        return services

    @property
    def callers(self) -> ChatCallerResolver:
        return self._callers

    @property
    def catalog(self) -> ChatModelCatalog:
        return self._catalog

    @property
    def tails(self) -> TailRunner:
        return self._tails

    @property
    def completions(self) -> ChatCompletionService:
        return self._completions

    @property
    def streamer(self) -> ChatStreamer:
        return self._streamer

    def admission(self) -> ChatAdmission:
        """Admission against the pool and queue as they are now."""
        return ChatAdmission(
            pool=self._pools.get_pool(),
            queue=self._queue_factory(),
            max_wait=settings.chat_queue_max_wait_seconds,
        )

    def context(self, *, caller: ChatCaller, lease: BackendLease, request: UpstreamChatRequest) -> ChatRequestContext:
        """The context one admitted request runs in."""
        return ChatRequestContext(
            caller=caller,
            request=request,
            lease=lease,
            pool=self._pools.get_pool(),
            usage_db=self._usage_db,
            queue=self._queue_factory(),
        )


def chat_services(request: Request) -> ChatServices:
    """The FastAPI dependency; tests replace it through `app.dependency_overrides`."""
    return ChatServices.of_app(request.app)
