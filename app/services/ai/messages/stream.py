"""A streamed Messages answer: relayed event by event, failed over only before the first, billed however it ends.

A failed OpenAI stream is restarted on another node with a `: retry`
comment between attempts, which igni's client understands. An Anthropic
client does not: a second `message_start` would read as a second message.
So a stream fails over only before its first event has been sent; after
that, a failure ends it with Anthropic's own `error` event, and the caller
decides.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import aclosing
import logging

from app.api.base_v1.schema.messages import AnthropicErrorBody
from app.services.ai.chat.errors import NoOtherNode
from app.services.ai.chat.tails import TailRunner
from app.services.ai.messages.context import MessagesContext
from app.services.ai.messages.upstream import MessagesUpstream
from app.services.ai.messages.usage import StreamTally
from app.services.ai.token_estimate import TokenEstimator
from app.services.ai.upstream import RetryPolicy, UpstreamFailure

logger = logging.getLogger(__name__)


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
                    self._upstream.stream(lease=self._ctx.lease, request=self._ctx.request, asked=self._ctx.asked)
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
            usage = self._tally.settle(estimator=self._estimator, request=self._ctx.request)
            await self._ctx.log_usage(usage, duration=self._tally.duration, stream=True)
        finally:
            await self._ctx.release()
