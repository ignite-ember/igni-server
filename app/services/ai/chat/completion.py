"""A non-streamed completion: retried on another node while another could fix it."""

from __future__ import annotations

import asyncio
import logging
import time

from app.api.base_v1.schema.chat import ChatCompletionResponse, CompletionUsage
from app.services.ai.chat.errors import NoOtherNode, UpstreamRefusal
from app.services.ai.chat.request_context import ChatRequestContext
from app.services.ai.chat.schemas import StreamUsage
from app.services.ai.chat.tails import TailRunner
from app.services.ai.chat.upstream_client import UpstreamChatClient
from app.services.ai.token_estimate import TokenEstimator
from app.services.ai.upstream import RetryPolicy, UpstreamFailure

logger = logging.getLogger(__name__)


class ChatCompletionService:
    """Answers one non-streamed chat request through the nodes."""

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

    async def complete(self, ctx: ChatRequestContext) -> ChatCompletionResponse:
        """One completion; a node that is gone or unwell is retried on another.

        Raises `UpstreamRefusal` for the last failure — not for a failed
        search for another node, which says only that we stopped looking.
        The lease comes back on every exit.
        """
        started = time.monotonic()
        attempt = 1
        try:
            while True:
                try:
                    return await self._attempt(ctx, started=started)
                except UpstreamFailure as failure:
                    failed_backend_id = ctx.lease.backend_id
                    logger.warning(
                        'Node %s did not answer usefully (%s): %s', failed_backend_id, type(failure).__name__, failure
                    )
                    if self._retry.gives_up(attempt=attempt, failure=failure):
                        raise UpstreamRefusal(failure) from failure
                    try:
                        await ctx.fail_over(failed_backend_id=failed_backend_id)
                    except NoOtherNode as exc:
                        raise UpstreamRefusal(failure) from exc
                    await self._retry.pause(attempt)
                    attempt += 1
        finally:
            await ctx.release()

    async def _attempt(self, ctx: ChatRequestContext, *, started: float) -> ChatCompletionResponse:
        try:
            completion = await self._upstream.complete(lease=ctx.lease, request=ctx.request)
        except asyncio.CancelledError:
            await self._tails.run(self._log_abandoned(ctx, started=started))
            raise
        await ctx.log_usage(completion.usage or CompletionUsage(), duration=time.monotonic() - started, stream=False)
        return completion.to_response()

    async def _log_abandoned(self, ctx: ChatRequestContext, *, started: float) -> None:
        """Usage for a completion its caller left: the prompt's prefill ran, so it is billed, estimated."""
        usage = StreamUsage(t0=started).settle(estimator=self._estimator, request=ctx.request)
        await ctx.log_usage(usage, duration=time.monotonic() - started, stream=False)
