"""A streamed completion: relayed frame by frame, retried before it gives up, billed however it ends."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import aclosing
import logging

from app.api.base_v1.schema.openai_error import OpenAIErrorBody
from app.services.ai.chat.errors import NoOtherNode
from app.services.ai.chat.request_context import ChatRequestContext
from app.services.ai.chat.schemas import StreamUsage
from app.services.ai.chat.tails import TailRunner
from app.services.ai.chat.upstream_client import UpstreamChatClient
from app.services.ai.token_estimate import TokenEstimator
from app.services.ai.upstream import RetryPolicy, UpstreamFailure

logger = logging.getLogger(__name__)

#: Sent before each retry: an SSE comment, so a client ignores it.
RETRY_FRAME = ': retry\n\n'


class CompletionStream:
    """One request's stream, and what it has used so far."""

    def __init__(
        self,
        *,
        ctx: ChatRequestContext,
        upstream: UpstreamChatClient,
        estimator: TokenEstimator,
        tails: TailRunner,
        retry: RetryPolicy,
    ) -> None:
        self._ctx = ctx
        self._upstream = upstream
        self._estimator = estimator
        self._tails = tails
        self._retry = retry
        self._used = StreamUsage()

    async def frames(self) -> AsyncGenerator[str, None]:
        """The frames to send. The tail runs on every exit, outside any cancellation."""
        attempts = self._attempts()
        try:
            async for frame in attempts:
                yield frame
        finally:
            await self._tails.run(self._finish(attempts))

    async def _attempts(self) -> AsyncGenerator[str, None]:
        """Each node in turn until one streams to the end, or a final error frame.

        A failure another node could not fix — a 4xx about the request —
        ends here rather than spending two more nodes on the same refusal.

        A failure raised while this generator is being closed or cancelled —
        the client left, and shutting the node's connection then failed — is not an
        attempt: it propagates to `_finish`, which logs it and still bills.
        Yielding a retry frame there would ignore the close.
        """
        attempt = 1
        while True:
            closing = False
            try:
                async with aclosing(self._relay()) as relayed:
                    async for frame in relayed:
                        try:
                            yield frame
                        except BaseException:  # closed or cancelled by the consumer, never the node
                            closing = True
                            raise
                return
            except UpstreamFailure as failure:
                if closing:
                    raise
                failed_backend_id = self._ctx.lease.backend_id
                if self._retry.gives_up(attempt=attempt, failure=failure):
                    logger.error('Upstream %s, giving up after attempt %d', failure.label, attempt)
                    yield OpenAIErrorBody.sse_frame(message=failure.sentence, status_code=failure.answer_status)
                    return
                logger.warning(
                    'Upstream %s on attempt %d/%d for %s (node=%s), retrying...',
                    failure.label,
                    attempt,
                    self._retry.max_retries,
                    self._ctx.model,
                    failed_backend_id,
                )
                yield RETRY_FRAME
                self._used.start_over()
                await self._retry.pause(attempt)
                attempt += 1
                await self._fail_over(failed_backend_id=failed_backend_id)

    async def _fail_over(self, *, failed_backend_id: str) -> None:
        """Move to another node; with none to take, retry on this one, and say so."""
        try:
            await self._ctx.fail_over(failed_backend_id=failed_backend_id)
        except NoOtherNode as exc:
            logger.warning('Could not take another node after %s failed; retrying on it: %s', failed_backend_id, exc)

    async def _relay(self) -> AsyncGenerator[str, None]:
        """One node's frames, counting what they carry."""
        async with aclosing(self._upstream.stream(lease=self._ctx.lease, request=self._ctx.request)) as lines:
            async for line in lines:
                if line.chunk is not None:
                    self._used.absorb(line.chunk)
                yield line.frame

    async def _finish(self, attempts: AsyncGenerator[str, None]) -> None:
        """Close the upstream read, record usage, free the node — once, however the stream ended.

        Closing mid-stream is what closes the node's connection, and vLLM
        aborts a request whose connection closes.
        """
        if self._used.recorded:
            return
        self._used.recorded = True
        ctx = self._ctx
        try:
            try:
                await attempts.aclose()
            except Exception as exc:  # noqa: BLE001 — named boundary: a failed close must never cost the usage row
                logger.warning('Closing the upstream stream failed: %r', exc)
            usage = self._used.settle(estimator=self._estimator, request=ctx.request)
            await ctx.log_usage(usage, duration=self._used.duration, stream=True)
        finally:
            await ctx.release()


class ChatStreamer:
    """Opens one `CompletionStream` per request."""

    def __init__(
        self,
        *,
        upstream: UpstreamChatClient,
        estimator: TokenEstimator,
        tails: TailRunner,
        retry: RetryPolicy,
    ) -> None:
        self._upstream = upstream
        self._estimator = estimator
        self._tails = tails
        self._retry = retry

    def open(self, ctx: ChatRequestContext) -> CompletionStream:
        return CompletionStream(
            ctx=ctx, upstream=self._upstream, estimator=self._estimator, tails=self._tails, retry=self._retry
        )
