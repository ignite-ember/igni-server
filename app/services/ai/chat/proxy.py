"""One chat request, from the caller's body to an answer: admitted, proxied, or abandoned."""

from __future__ import annotations

from app.api.base_v1.schema.chat import ChatCompletionRequest
from app.config import settings
from app.db.sql.chat_usage import ChatUsageDB
from app.services.ai.chat.admission import ChatAdmission
from app.services.ai.chat.answers import CallerLeft, ChatAnswer, CompletedAnswer, StreamedAnswer
from app.services.ai.chat.callers import ChatCallerResolver
from app.services.ai.chat.completion import ChatCompletionService
from app.services.ai.chat.presence import CallerPresence
from app.services.ai.chat.request_context import ChatRequestContext
from app.services.ai.chat.schemas import ChatCaller, UpstreamChatRequest
from app.services.ai.chat.streaming import ChatStreamer
from app.services.ai.fair_queue import FairQueue
from app.services.ai.pool_manager import PoolManager
from app.services.ai.schemas import BackendLease


class ChatProxy:
    """Chooses the model, admits the request to a node, and streams or completes it.

    The pool is looked up per request, so configuration read at request
    time still applies; the queue is the app's one. Refusals are raised as
    `ChatRefused` and left to the caller to translate.
    """

    def __init__(
        self,
        *,
        callers: ChatCallerResolver,
        pools: PoolManager,
        queue: FairQueue,
        usage_db: ChatUsageDB,
        streamer: ChatStreamer,
        completions: ChatCompletionService,
    ) -> None:
        self._callers = callers
        self._pools = pools
        self._queue = queue
        self._usage_db = usage_db
        self._streamer = streamer
        self._completions = completions

    async def answer(self, *, caller: ChatCaller, body: ChatCompletionRequest, presence: CallerPresence) -> ChatAnswer:
        """The answer to *body*; `CallerLeft` when the caller left while it was admitted or generated.

        Admission and completion are each cancelled on their own if the
        caller leaves. A stream is returned unstarted.
        """
        model = await self._callers.preferred_model(caller=caller, requested=body.model)
        lease = await presence.unless_it_closes(self._admission().admit(caller=caller, model=model))
        if lease is None:
            return CallerLeft()
        upstream_request = UpstreamChatRequest.for_upstream(body, model=model or lease.model)
        ctx = self._context(caller=caller, lease=lease, request=upstream_request)
        if body.stream:
            return StreamedAnswer(stream=self._streamer.open(ctx))
        response = await presence.unless_it_closes(self._completions.complete(ctx))
        if response is None:
            return CallerLeft()
        return CompletedAnswer(response=response)

    def _admission(self) -> ChatAdmission:
        """Admission against the pool as it is now."""
        return ChatAdmission(
            pool=self._pools.get_pool(),
            queue=self._queue,
            max_wait=settings.chat_queue_max_wait_seconds,
        )

    def _context(self, *, caller: ChatCaller, lease: BackendLease, request: UpstreamChatRequest) -> ChatRequestContext:
        """The context one admitted request runs in."""
        return ChatRequestContext(
            caller=caller,
            request=request,
            lease=lease,
            pool=self._pools.get_pool(),
            usage_db=self._usage_db,
            queue=self._queue,
        )
