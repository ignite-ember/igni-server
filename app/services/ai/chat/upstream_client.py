"""The HTTP call to one inference node, streamed or not.

Every transport error is an `UpstreamFailure` past this boundary, so the
callers retry on one exception family and never see `httpx`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
import logging

import httpx
from pydantic import ValidationError

from app.config import settings
from app.services.ai.chat.schemas import UpstreamChatRequest, UpstreamCompletion, UpstreamLine
from app.services.ai.request_shape import describe as describe_chat_request
from app.services.ai.schemas import BackendLease
from app.services.ai.sse_keepalive import lines_with_keepalive
from app.services.ai.upstream import UpstreamError, UpstreamFailure, UpstreamUnreadable

logger = logging.getLogger(__name__)

#: A stream's timeouts. `read` is per chunk; `sse_keepalive` decides when
#: a silence means the node is gone.
STREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)

#: Builds the `httpx` client for one call; tests hand in one over a mock transport.
ClientFactory = Callable[..., httpx.AsyncClient]


class UpstreamChatClient:
    """Sends one chat request to one node."""

    def __init__(
        self,
        *,
        client_factory: ClientFactory = httpx.AsyncClient,
        complete_timeout: float | None = None,
        stream_timeout: httpx.Timeout = STREAM_TIMEOUT,
    ) -> None:
        self._client_factory = client_factory
        self._complete_timeout = complete_timeout if complete_timeout is not None else settings.ai.upstream_timeout
        self._stream_timeout = stream_timeout

    async def complete(self, *, lease: BackendLease, request: UpstreamChatRequest) -> UpstreamCompletion:
        """One non-streamed completion from the node behind *lease*.

        Raises `UpstreamError` for a non-200, `UpstreamUnreadable` for a
        200 that is not a completion, and a transport failure otherwise.
        """
        try:
            async with self._client_factory(timeout=self._complete_timeout) as client:
                resp = await client.post(lease.completions_url, headers=lease.auth_headers(), json=request.wire())
        except httpx.RequestError as exc:
            raise UpstreamFailure.from_transport(exc) from exc

        if resp.status_code != 200:
            logger.error('Upstream AI error %s: %s', resp.status_code, resp.text[:500])
            raise UpstreamError(resp.status_code, resp.text[:500])

        try:
            return UpstreamCompletion.from_raw(resp.json())
        except (ValueError, ValidationError) as exc:
            logger.error('Upstream returned %s with an unparseable body: %r', resp.status_code, resp.text[:200])
            raise UpstreamUnreadable(resp.text[:200]) from exc

    async def stream(self, *, lease: BackendLease, request: UpstreamChatRequest) -> AsyncGenerator[UpstreamLine, None]:
        """The node's SSE lines, keepalives included, each ready to forward.

        Raises `UpstreamError` before the first line for a non-200, and
        `UpstreamStale` when the stream goes silent for too long.
        """
        wire = request.wire()
        try:
            async with self._client_factory(timeout=self._stream_timeout) as client:
                async with client.stream(
                    'POST', lease.completions_url, headers=lease.auth_headers(), json=wire
                ) as resp:
                    if resp.status_code != 200:
                        error_body = await resp.aread()
                        logger.error('Upstream AI stream error %s: %s', resp.status_code, error_body[:500])
                        if resp.status_code == 400:
                            # The shape, never the prompt: it answers what a 400 is about.
                            logger.error('Upstream 400 — request %s', describe_chat_request(wire))
                        raise UpstreamError(resp.status_code, error_body)

                    async for line in lines_with_keepalive(resp):
                        upstream_line = UpstreamLine.from_sse(line)
                        if upstream_line is not None:
                            yield upstream_line
        except httpx.RequestError as exc:
            raise UpstreamFailure.from_transport(exc) from exc
