"""Fakes for driving the chat proxy (`app/services/ai/chat`) without a node, Redis or a database."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from app.api.base_v1.schema.chat import ChatCompletionRequest
from app.services.ai.chat.callers import ChatCallerResolver
from app.services.ai.chat.completion import ChatCompletionService
from app.services.ai.chat.request_context import ChatRequestContext
from app.services.ai.chat.schemas import ChatCaller, UpstreamChatRequest, UpstreamLine
from app.services.ai.chat.streaming import ChatStreamer
from app.services.ai.chat.tails import TailRunner
from app.services.ai.chat.upstream_client import UpstreamChatClient
from app.services.ai.fair_queue import FairQueue
from app.services.ai.schemas import BackendLease
from app.services.ai.token_estimate import TokenEstimator
from app.services.ai.upstream import RetryPolicy
from app.services.auth.org_context import OrgContext

#: No pause between attempts: the tests are about which attempts, not when.
NO_BACKOFF = RetryPolicy(backoff=0)


def lease(n: int = 1, *, model: str = 'fake-model') -> BackendLease:
    return BackendLease(
        lease_id=f'lease-{n}', backend_id=f'node-{n}', base_url='http://node.invalid/v1', model=model, api_key=None
    )


@dataclass
class SpyPool:
    """Hands out a new lease on each acquire, and records every release."""

    acquired: list[str] = field(default_factory=lambda: ['lease-1'])
    released: list[str] = field(default_factory=list)
    model: str = 'fake-model'

    async def acquire(
        self, user_id: str, *, model: str | None = None, exclude_backend_id: str | None = None, wire=None
    ):
        nxt = len(self.acquired) + 1
        self.acquired.append(f'lease-{nxt}')
        return lease(nxt, model=self.model)

    async def release(self, held: BackendLease) -> None:
        self.released.append(held.lease_id)


@dataclass
class UsageRows:
    """A usage table that keeps what it is given."""

    rows: list[dict] = field(default_factory=list)

    async def log(self, **row) -> None:
        self.rows.append(row)


def request(messages: list[dict] | None = None, *, stream: bool = False, model: str = 'fake-model'):
    body = ChatCompletionRequest(messages=messages or [{'role': 'user', 'content': 'hi'}], stream=stream)
    return UpstreamChatRequest.for_upstream(body, model=model)


def context(pool: SpyPool, *, usage_db=None, upstream_request=None, caller: ChatCaller | None = None):
    """A real `ChatRequestContext` over fakes."""
    return ChatRequestContext(
        caller=caller or ChatCaller(user_id='user-1', org_id='org-1'),
        request=upstream_request or request(),
        lease=lease(1, model=pool.model),
        pool=pool,
        usage_db=usage_db if usage_db is not None else UsageRows(),
        queue=FairQueue(max_length=10),
    )


def completions(upstream: UpstreamChatClient, *, retry: RetryPolicy = NO_BACKOFF) -> ChatCompletionService:
    return ChatCompletionService(upstream=upstream, estimator=TokenEstimator(), tails=TailRunner(), retry=retry)


def streamer(upstream, *, retry: RetryPolicy = NO_BACKOFF) -> ChatStreamer:
    return ChatStreamer(upstream=upstream, estimator=TokenEstimator(), tails=TailRunner(), retry=retry)


def on_transport(handler: Callable[[httpx.Request], httpx.Response]) -> UpstreamChatClient:
    """A real client whose every call lands on *handler*."""
    return UpstreamChatClient(
        client_factory=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw),
        complete_timeout=5,
    )


def sse(content: str) -> str:
    """One SSE data line carrying *content*."""
    chunk = {
        'id': 'c',
        'object': 'chat.completion.chunk',
        'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': None}],
    }
    return f'data: {json.dumps(chunk)}'


class ScriptedStreams:
    """An upstream whose stream, per attempt, yields lines and then optionally raises."""

    def __init__(self, *attempts: tuple[list[str], Exception | None]) -> None:
        self._attempts = list(attempts)
        self.calls: list[BackendLease] = []
        self.models: list[str] = []

    async def stream(self, *, lease: BackendLease, request: UpstreamChatRequest) -> AsyncGenerator[UpstreamLine, None]:
        lines, failure = self._attempts[min(len(self.calls), len(self._attempts) - 1)]
        self.calls.append(lease)
        self.models.append(request.model)
        for line in lines:
            parsed = UpstreamLine.from_sse(line)
            if parsed is not None:
                yield parsed
        if failure is not None:
            raise failure


async def drain(frames) -> list[str]:
    return [frame async for frame in frames]


def person(user_id: str = 'ada', org_id: str | None = 'org-1') -> ChatCallerResolver:
    """A resolver that takes every token for *user_id* in *org_id*, with no group model."""
    return ChatCallerResolver(
        producers=SimpleNamespace(machine=AsyncMock(return_value=None)),
        orgs=SimpleNamespace(resolve=AsyncMock(return_value=OrgContext(user_id=user_id, org_id=org_id))),
        group_models=SimpleNamespace(model_for_user=AsyncMock(return_value=None)),
    )


def pools(pool) -> SimpleNamespace:
    """A pool manager that hands out *pool*."""
    return SimpleNamespace(get_pool=lambda: pool)
