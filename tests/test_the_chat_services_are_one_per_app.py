"""The chat proxy's parts are built once per app, and read their configuration per request.

They were module globals in the chat route — a usage table, a set of
tails, the pool manager — and the queue was looked up on each request.
`ChatServices` builds them once and hangs them on the app. What must
not move with that: the longest a request waits for a node is read
when it asks, and a freed slot still wakes whoever waits next.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import FastAPI
import httpx

from app.api.base_v1.schema.chat import ChatCompletionRequest
from app.config import settings
from app.services.ai.chat.answers import CompletedAnswer
from app.services.ai.chat.schemas import ChatCaller
from app.services.ai.chat.services import chat_services, ChatServices
from app.services.ai.schemas import BackendLease
from tests.chat_proxy import NO_BACKOFF, on_transport, person, pools, SpyPool, UsageRows


class TestOnePerApp:
    def test_the_first_use_builds_it_and_every_later_one_gets_the_same(self):
        app = FastAPI()

        first = ChatServices.of_app(app)

        assert ChatServices.of_app(app) is first
        assert app.state.chat_services is first
        assert ChatServices.of_app(FastAPI()) is not first

    def test_the_route_dependency_is_the_apps_instance(self):
        app = FastAPI()

        assert chat_services(SimpleNamespace(app=app)) is ChatServices.of_app(app)


class _Queue:
    """A queue with room for everyone, which remembers how long each caller may wait and each kick."""

    def __init__(self) -> None:
        self.max_waits: list[float] = []
        self.kicks = 0

    async def take(self, *, acquire, max_wait: float, user_id: str, model: str | None, **_kwargs) -> BackendLease:
        self.max_waits.append(max_wait)
        return await acquire(user_id, model)

    def kick(self) -> None:
        self.kicks += 1


class _Present:
    async def unless_it_closes(self, work):
        return await work


def _services(queue: _Queue) -> ChatServices:
    node = on_transport(lambda _request: httpx.Response(200, text=json.dumps({'id': 'x', 'choices': []})))
    return ChatServices(
        pools=pools(SpyPool()),
        queue=queue,
        usage_db=UsageRows(),
        upstream=node,
        retry=NO_BACKOFF,
        callers=person(),
    )


async def _ask(services: ChatServices):
    return await services.proxy.answer(
        caller=ChatCaller(user_id='ada', org_id='org-1'),
        body=ChatCompletionRequest(messages=[{'role': 'user', 'content': 'hi'}]),
        presence=_Present(),
    )


class TestPerRequest:
    async def test_the_longest_wait_for_a_node_is_read_when_a_request_asks(self, monkeypatch):
        queue = _Queue()
        services = _services(queue)

        monkeypatch.setattr(settings, 'chat_queue_max_wait_seconds', 7.0)
        await _ask(services)
        monkeypatch.setattr(settings, 'chat_queue_max_wait_seconds', 2.0)
        await _ask(services)

        assert queue.max_waits == [7.0, 2.0]

    async def test_a_finished_request_wakes_the_queue(self):
        queue = _Queue()

        answer = await _ask(_services(queue))

        assert isinstance(answer, CompletedAnswer)
        assert queue.kicks == 1
