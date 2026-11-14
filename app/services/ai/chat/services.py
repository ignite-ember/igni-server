"""The chat proxy's composition root: one per app, handed to the router as a dependency."""

from __future__ import annotations

from fastapi import Request
from starlette.applications import Starlette

from app.config import settings
from app.db.sql.chat_usage import ChatUsageDB
from app.security.producer import Producers
from app.services.ai.chat.callers import ChatCallerResolver
from app.services.ai.chat.catalog import ChatModelCatalog
from app.services.ai.chat.completion import ChatCompletionService
from app.services.ai.chat.proxy import ChatProxy
from app.services.ai.chat.streaming import ChatStreamer
from app.services.ai.chat.tails import TailRunner
from app.services.ai.chat.upstream_client import UpstreamChatClient
from app.services.ai.fair_queue import FairQueue
from app.services.ai.messages.proxy import MessagesProxy
from app.services.ai.messages.upstream import MessagesUpstream
from app.services.ai.pool_manager import PoolManager
from app.services.ai.providers.credentials import CloudCredentials
from app.services.ai.token_estimate import TokenEstimator
from app.services.ai.upstream import RetryPolicy
from app.services.auth.org_context import OrgContexts
from app.services.portal.group_model_service import GroupModelService


class ChatServices:
    """Builds the chat proxy's parts once and wires them together.

    Long-lived on purpose: the tail runner must outlive a request so a
    shielded tail is never collected, the model catalog caches, and the
    fair queue holds every waiting request of this replica. The proxy
    looks the pool up per request, so configuration read at request
    time still applies.
    """

    def __init__(
        self,
        *,
        pools: PoolManager | None = None,
        queue: FairQueue | None = None,
        usage_db: ChatUsageDB | None = None,
        estimator: TokenEstimator | None = None,
        tails: TailRunner | None = None,
        upstream: UpstreamChatClient | None = None,
        retry: RetryPolicy | None = None,
        callers: ChatCallerResolver | None = None,
        catalog: ChatModelCatalog | None = None,
        credentials: CloudCredentials | None = None,
        messages_upstream: MessagesUpstream | None = None,
    ) -> None:
        pools = pools or PoolManager()
        self._tails = tails or TailRunner()
        self._callers = callers or ChatCallerResolver(
            producers=Producers(), orgs=OrgContexts(), group_models=GroupModelService()
        )
        self._catalog = catalog or ChatModelCatalog(pools=pools)
        credentials = credentials or CloudCredentials()
        upstream = upstream or UpstreamChatClient(credentials=credentials)
        estimator = estimator or TokenEstimator()
        retry = retry or RetryPolicy()
        # One queue for both APIs: a replica's waiting requests are fair to each other whichever they speak.
        queue = queue or FairQueue(max_length=settings.chat_queue_max_length)
        usage_db = usage_db or ChatUsageDB()
        self._messages = MessagesProxy(
            pools=pools,
            queue=queue,
            usage_db=usage_db,
            upstream=messages_upstream or MessagesUpstream(credentials=credentials),
            estimator=estimator,
            tails=self._tails,
            retry=retry,
            max_wait=settings.chat_queue_max_wait_seconds,
        )
        self._proxy = ChatProxy(
            callers=self._callers,
            pools=pools,
            queue=queue,
            usage_db=usage_db,
            streamer=ChatStreamer(upstream=upstream, estimator=estimator, tails=self._tails, retry=retry),
            completions=ChatCompletionService(upstream=upstream, estimator=estimator, tails=self._tails, retry=retry),
        )

    @classmethod
    def of_app(cls, app: Starlette) -> ChatServices:
        """The app's instance, built on first use, so it works with or without a lifespan."""
        services: ChatServices | None = getattr(app.state, 'chat_services', None)
        if services is None:
            services = app.state.chat_services = cls(credentials=CloudCredentials.of_app(app))
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
    def proxy(self) -> ChatProxy:
        return self._proxy

    @property
    def messages(self) -> MessagesProxy:
        """`/v1/messages`: the Anthropic API's proxy, sharing this replica's queue and usage table."""
        return self._messages


def chat_services(request: Request) -> ChatServices:
    """The FastAPI dependency; tests replace it through `app.dependency_overrides`."""
    return ChatServices.of_app(request.app)
