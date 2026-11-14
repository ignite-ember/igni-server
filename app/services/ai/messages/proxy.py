"""One Messages request, from the caller's body to an answer: admitted, proxied, billed, released.

The same shape as the OpenAI side (`chat/proxy.py`). The admitted request
lives in `context.py`; a streamed answer, and why it fails over only
before its first event, in `stream.py`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.api.base_v1.schema.messages import MessagesRequest
from app.db.sql.chat_usage import ChatUsageDB
from app.services.ai.chat.callers import ChatCallerResolver
from app.services.ai.chat.errors import NoOtherNode, UpstreamRefusal
from app.services.ai.chat.presence import CallerPresence
from app.services.ai.chat.schemas import ChatCaller
from app.services.ai.chat.tails import TailRunner
from app.services.ai.fair_queue import FairQueue
from app.services.ai.messages.calls import CallerHeaders
from app.services.ai.messages.context import MessagesAdmission, MessagesContext
from app.services.ai.messages.events import ProviderMessage
from app.services.ai.messages.stream import MessagesStream
from app.services.ai.messages.upstream import MessagesUpstream
from app.services.ai.messages.usage import MessagesUsage
from app.services.ai.pool_manager import PoolManager
from app.services.ai.token_estimate import TokenEstimator
from app.services.ai.upstream import RetryPolicy, UpstreamFailure

logger = logging.getLogger(__name__)


class _Answer(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)


class MessagesCallerLeft(_Answer):
    """The caller left while waiting; nothing is owed them."""


class StreamedMessage(_Answer):
    """A stream, opened and not yet started."""

    stream: MessagesStream


class CompletedMessage(_Answer):
    """The provider's whole message, as the JSON it sent: forwarded untouched, so a raw object."""

    message: dict[str, Any]


type MessagesAnswer = MessagesCallerLeft | StreamedMessage | CompletedMessage


class MessagesProxy:
    """Admits a Messages request to a backend speaking Anthropic's API, and streams or completes it."""

    def __init__(
        self,
        *,
        callers: ChatCallerResolver,
        pools: PoolManager,
        queue: FairQueue,
        usage_db: ChatUsageDB,
        upstream: MessagesUpstream,
        estimator: TokenEstimator,
        tails: TailRunner,
        retry: RetryPolicy,
        max_wait: float,
    ) -> None:
        self._max_wait = max_wait
        self._callers = callers
        self._pools = pools
        self._queue = queue
        self._usage_db = usage_db
        self._upstream = upstream
        self._estimator = estimator
        self._tails = tails
        self._retry = retry

    async def answer(
        self, *, caller: ChatCaller, body: MessagesRequest, asked: CallerHeaders, presence: CallerPresence
    ) -> MessagesAnswer:
        """The answer to *body*; `MessagesCallerLeft` when the caller left while it was admitted or generated."""
        admission = MessagesAdmission(pool=self._pools.get_pool(), queue=self._queue, max_wait=self._max_wait)
        lease = await presence.unless_it_closes(admission.admit(caller=caller, model=body.model))
        if lease is None:
            return MessagesCallerLeft()
        ctx = MessagesContext(
            caller=caller,
            body=body.wire(),
            asked=asked,
            lease=lease,
            pools=self._pools,
            usage_db=self._usage_db,
            queue=self._queue,
        )
        if body.stream:
            return StreamedMessage(
                stream=MessagesStream(
                    ctx=ctx, upstream=self._upstream, estimator=self._estimator, tails=self._tails, retry=self._retry
                )
            )
        message = await presence.unless_it_closes(self._complete(ctx))
        if message is None:
            return MessagesCallerLeft()
        return CompletedMessage(message=message)

    async def _complete(self, ctx: MessagesContext) -> dict[str, Any]:
        """One message; a backend that is gone or unwell is retried on another. The lease comes back on every exit."""
        started = time.monotonic()
        attempt = 1
        try:
            while True:
                try:
                    message = await self._upstream.complete(lease=ctx.lease, body=ctx.body, asked=ctx.asked)
                except asyncio.CancelledError:
                    await self._tails.run(self._log_abandoned(ctx, started=started))
                    raise
                except UpstreamFailure as failure:
                    if self._retry.gives_up(attempt=attempt, failure=failure):
                        raise UpstreamRefusal(failure) from failure
                    try:
                        await ctx.fail_over(failed_backend_id=ctx.lease.backend_id)
                    except NoOtherNode as exc:
                        raise UpstreamRefusal(failure) from exc
                    await self._retry.pause(attempt)
                    attempt += 1
                    continue
                usage = MessagesUsage.of(ProviderMessage.model_validate(message).usage)
                if not usage.reported:
                    usage = MessagesUsage.estimated(self._estimator, body=ctx.body)
                await ctx.log_usage(usage, duration=time.monotonic() - started, stream=False)
                return message
        finally:
            await ctx.release()

    async def _log_abandoned(self, ctx: MessagesContext, *, started: float) -> None:
        """A message its caller left: the prompt ran, so it is billed, estimated."""
        estimated = MessagesUsage.estimated(self._estimator, body=ctx.body)
        await ctx.log_usage(estimated, duration=time.monotonic() - started, stream=False)
