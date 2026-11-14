"""One Messages request, from the caller's body to an answer: admitted, proxied, billed, released.

The same shape as the OpenAI side (`chat/proxy.py`) with one difference
that matters. A failed OpenAI stream is restarted on another node with a
`: retry` comment between attempts, which igni's client understands. An
Anthropic client does not: a second `message_start` would read as a
second message. So a stream fails over only before its first event has
been sent; after that, a failure ends it with Anthropic's own `error`
event, and the caller decides.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
import logging
import time
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.api.base_v1.schema.messages import AnthropicErrorBody, MessagesRequest
from app.config import settings
from app.db.sql.chat_usage import ChatUsageDB
from app.services.ai.chat.admission import ChatAdmission
from app.services.ai.chat.callers import ChatCallerResolver
from app.services.ai.chat.errors import NoOtherNode, UpstreamRefusal
from app.services.ai.chat.presence import CallerPresence
from app.services.ai.chat.schemas import ChatCaller
from app.services.ai.chat.tails import TailRunner
from app.services.ai.fair_queue import FairQueue
from app.services.ai.messages.calls import CallerHeaders
from app.services.ai.messages.upstream import MessagesUpstream
from app.services.ai.messages.usage import estimate, MessagesUsage, StreamTally
from app.services.ai.pool_manager import PoolManager
from app.services.ai.providers.kinds import Wire
from app.services.ai.schemas import BackendLease
from app.services.ai.token_estimate import TokenEstimator
from app.services.ai.upstream import RetryPolicy, UpstreamFailure

logger = logging.getLogger(__name__)


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
        try:
            await pool.release(self.lease)
            self.lease = await pool.acquire(
                self.caller.user_id, model=self.model, exclude_backend_id=failed_backend_id, wire=Wire.ANTHROPIC
            )
        except Exception as exc:  # noqa: BLE001 — named boundary: nothing left to try
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


class MessagesStream:
    """One request's stream: relayed event by event, failed over only before the first, billed however it ends."""

    def __init__(
        self,
        *,
        ctx: MessagesContext,
        upstream: MessagesUpstream,
        estimator: TokenEstimator,
        tails: TailRunner,
        retry: RetryPolicy,
    ) -> None:
        self._ctx = ctx
        self._upstream = upstream
        self._estimator = estimator
        self._tails = tails
        self._retry = retry
        self._tally = StreamTally()

    async def frames(self) -> AsyncGenerator[str, None]:
        """The frames to send. The tail runs on every exit, outside any cancellation."""
        attempts = self._attempts()
        try:
            async for frame in attempts:
                yield frame
        finally:
            await self._tails.run(self._finish(attempts))

    async def _attempts(self) -> AsyncGenerator[str, None]:
        attempt = 1
        started = False
        while True:
            closing = False
            try:
                async with aclosing(
                    self._upstream.stream(lease=self._ctx.lease, body=self._ctx.body, asked=self._ctx.asked)
                ) as events:
                    async for event in events:
                        if event.event is not None:
                            self._tally.absorb(event.event)
                            started = True
                        try:
                            yield event.frame
                        except BaseException:  # closed or cancelled by the consumer, never the provider
                            closing = True
                            raise
                return
            except UpstreamFailure as failure:
                if closing:
                    raise
                failed_backend_id = self._ctx.lease.backend_id
                if started or self._retry.gives_up(attempt=attempt, failure=failure):
                    logger.error('Upstream %s on %s, ending the stream', failure.label, failed_backend_id)
                    yield AnthropicErrorBody.sse_frame(message=failure.sentence, status_code=failure.answer_status)
                    return
                logger.warning(
                    'Upstream %s on attempt %d for %s, failing over', failure.label, attempt, failed_backend_id
                )
                self._tally.start_over()
                await self._retry.pause(attempt)
                attempt += 1
                try:
                    await self._ctx.fail_over(failed_backend_id=failed_backend_id)
                except NoOtherNode as exc:
                    logger.warning('No other backend after %s failed; retrying it: %s', failed_backend_id, exc)

    async def _finish(self, attempts: AsyncGenerator[str, None]) -> None:
        if self._tally.recorded:
            return
        self._tally.recorded = True
        try:
            try:
                await attempts.aclose()
            except Exception as exc:  # noqa: BLE001 — a failed close must never cost the usage row
                logger.warning('Closing the upstream stream failed: %r', exc)
            usage = self._tally.settle(estimator=self._estimator, body=self._ctx.body)
            await self._ctx.log_usage(usage, duration=self._tally.duration, stream=True)
        finally:
            await self._ctx.release()


class _Answer(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)


class MessagesCallerLeft(_Answer):
    """The caller left while waiting; nothing is owed them."""


class StreamedMessage(_Answer):
    """A stream, opened and not yet started."""

    stream: MessagesStream


class CompletedMessage(_Answer):
    """The provider's whole message."""

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
    ) -> None:
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
        admission = MessagesAdmission(
            pool=self._pools.get_pool(), queue=self._queue, max_wait=settings.chat_queue_max_wait_seconds
        )
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
                usage = MessagesUsage.of_message(message)
                if not usage.reported:
                    usage = estimate(self._estimator, body=ctx.body)
                await ctx.log_usage(usage, duration=time.monotonic() - started, stream=False)
                return message
        finally:
            await ctx.release()

    async def _log_abandoned(self, ctx: MessagesContext, *, started: float) -> None:
        """A message its caller left: the prompt ran, so it is billed, estimated."""
        await ctx.log_usage(estimate(self._estimator, body=ctx.body), duration=time.monotonic() - started, stream=False)
