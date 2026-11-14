"""The HTTP call behind `/v1/messages`, streamed or not, for every Claude kind.

Every transport error is an `UpstreamFailure` past this boundary, as on
the OpenAI side, so the proxy retries on one exception family. A token
the provider refuses (401/403) is renewed once, on the same backend,
before the refusal counts.

Bedrock streams in AWS's binary event stream rather than server-sent
events: each `chunk` event carries one Anthropic event, base64-encoded,
and an exception event carries the failure. They are decoded here and
sent on as the SSE events every other kind sends, so the caller cannot
tell which served it.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass
import json
import logging
from typing import Any

import httpx

from app.config import settings
from app.services.ai.messages.calls import CallerHeaders, MessagesCall, prepare
from app.services.ai.providers.calls import forget_token, renews
from app.services.ai.providers.credentials import CloudCredentials, shared_credentials
from app.services.ai.providers.kinds import BackendKind
from app.services.ai.schemas import BackendLease
from app.services.ai.sse_keepalive import KEEPALIVE_LINE, lines_with_keepalive
from app.services.ai.upstream import UpstreamError, UpstreamFailure, UpstreamUnreadable

logger = logging.getLogger(__name__)

#: A stream's timeouts, as on the OpenAI side: `read` is per chunk.
STREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)

#: Bedrock's exception events, as the status the same failure has over HTTP.
_BEDROCK_EXCEPTIONS = {
    'throttlingException': 429,
    'serviceUnavailableException': 503,
    'validationException': 400,
    'modelTimeoutException': 504,
    'modelStreamErrorException': 502,
    'internalServerException': 500,
}

ClientFactory = Callable[..., httpx.AsyncClient]


@dataclass(frozen=True)
class StreamEvent:
    """One event to forward: the SSE frame as sent, and the event parsed, when it is one."""

    frame: str
    event: dict[str, Any] | None = None


class MessagesUpstream:
    """Sends one Messages request to one backend."""

    def __init__(
        self,
        *,
        client_factory: ClientFactory = httpx.AsyncClient,
        credentials: CloudCredentials | None = None,
        stream_timeout: httpx.Timeout = STREAM_TIMEOUT,
    ) -> None:
        self._client_factory = client_factory
        self._credentials = credentials if credentials is not None else shared_credentials()
        self._stream_timeout = stream_timeout

    async def complete(self, *, lease: BackendLease, body: dict[str, Any], asked: CallerHeaders) -> dict[str, Any]:
        """One non-streamed answer: the provider's message, as JSON."""
        for attempt in (1, 2):
            call = await self._prepare(lease, body, stream=False, asked=asked)
            try:
                async with self._client_factory(timeout=settings.ai.upstream_timeout) as client:
                    resp = await client.post(call.url, headers=call.headers, content=call.content)
            except httpx.RequestError as exc:
                raise UpstreamFailure.from_transport(exc) from exc
            if not self._renew_token(lease, resp.status_code, attempt):
                break
        if resp.status_code != 200:
            logger.error('Upstream Messages error %s from %s: %s', resp.status_code, lease.backend_id, resp.text[:500])
            raise UpstreamError(resp.status_code, resp.text[:500])
        try:
            message = resp.json()
        except ValueError as exc:
            raise UpstreamUnreadable(resp.text[:200]) from exc
        if not isinstance(message, dict) or message.get('type') != 'message':
            raise UpstreamUnreadable(resp.text[:200])
        return message

    async def stream(
        self, *, lease: BackendLease, body: dict[str, Any], asked: CallerHeaders
    ) -> AsyncGenerator[StreamEvent, None]:
        """The provider's events as SSE frames, keepalives included."""
        try:
            for attempt in (1, 2):
                call = await self._prepare(lease, body, stream=True, asked=asked)
                async with self._client_factory(timeout=self._stream_timeout) as client:
                    async with client.stream('POST', call.url, headers=call.headers, content=call.content) as resp:
                        if self._renew_token(lease, resp.status_code, attempt):
                            continue
                        if resp.status_code != 200:
                            error_body = await resp.aread()
                            logger.error(
                                'Upstream Messages stream error %s from %s: %s',
                                resp.status_code,
                                lease.backend_id,
                                error_body[:500],
                            )
                            raise UpstreamError(resp.status_code, error_body)
                        source = _BedrockLines(resp) if lease.kind is BackendKind.BEDROCK else resp
                        async for event in _events(lines_with_keepalive(source)):
                            yield event
                        return
        except httpx.RequestError as exc:
            raise UpstreamFailure.from_transport(exc) from exc

    async def _prepare(
        self, lease: BackendLease, body: dict[str, Any], *, stream: bool, asked: CallerHeaders
    ) -> MessagesCall:
        return await prepare(lease, body, stream=stream, asked=asked, credentials=self._credentials)

    def _renew_token(self, lease: BackendLease, status: int, attempt: int) -> bool:
        """Whether *status* was a refused credential worth renewing once; drops it if so."""
        if attempt == 1 and status in (401, 403) and renews(lease):
            logger.info('Backend %s refused its credentials (%s); renewing them once', lease.backend_id, status)
            forget_token(lease, self._credentials)
            return True
        return False


async def _events(lines: AsyncIterator[str]) -> AsyncGenerator[StreamEvent, None]:
    """SSE lines grouped into events: each blank line ends one. Keepalives go on their own."""
    pending: list[str] = []
    async for line in lines:
        if line == KEEPALIVE_LINE:
            yield StreamEvent(frame=f'{line}\n\n')
            continue
        if line:
            pending.append(line)
            continue
        if pending:
            yield _event_of(pending)
            pending = []
    if pending:
        yield _event_of(pending)


def _event_of(lines: list[str]) -> StreamEvent:
    data = next((line[len('data:') :].strip() for line in lines if line.startswith('data:')), None)
    event: dict[str, Any] | None = None
    if data:
        try:
            parsed = json.loads(data)
            event = parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            event = None
    return StreamEvent(frame='\n'.join(lines) + '\n\n', event=event)


class _BedrockLines:
    """A Bedrock event-stream response, read as the SSE lines Anthropic would have sent."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    def aiter_lines(self) -> AsyncIterator[str]:
        return self._lines()

    async def _lines(self) -> AsyncGenerator[str, None]:
        from botocore.eventstream import EventStreamBuffer

        buffer = EventStreamBuffer()
        async for chunk in self._response.aiter_bytes():
            buffer.add_data(chunk)
            for message in buffer:
                for line in _bedrock_message(message.headers, message.payload):
                    yield line


def _bedrock_message(headers: dict[str, Any], payload: bytes) -> list[str]:
    """One event-stream message as SSE lines; an exception event raises as the HTTP failure it is."""
    if headers.get(':message-type') in ('exception', 'error'):
        name = str(headers.get(':exception-type') or headers.get(':error-code') or 'internalServerException')
        raise UpstreamError(_BEDROCK_EXCEPTIONS.get(name, 502), payload[:500])
    if headers.get(':event-type') != 'chunk':
        return []
    try:
        inner = json.loads(base64.b64decode(json.loads(payload)['bytes']))
    except (ValueError, KeyError, TypeError) as exc:
        raise UpstreamUnreadable(payload[:200].decode(errors='replace')) from exc
    return [f'event: {inner.get("type", "message")}', f'data: {json.dumps(inner)}', '']
