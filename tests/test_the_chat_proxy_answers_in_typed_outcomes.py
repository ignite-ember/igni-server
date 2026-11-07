"""`ChatProxy.answer` says what happened as a typed outcome, never as None.

A caller who leaves while waiting for a node, or while the completion is
generated, is `CallerLeft`; a stream is `StreamedAnswer`, unstarted; a
completion is `CompletedAnswer`. With no model asked for, the node's own
model is the one sent upstream.
"""

from __future__ import annotations

from collections.abc import Coroutine
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

from app.api.base_v1.schema.chat import ChatCompletionRequest, ChatCompletionResponse
from app.services.ai.chat.answers import CallerLeft, CompletedAnswer, StreamedAnswer
from app.services.ai.chat.proxy import ChatProxy
from app.services.ai.chat.schemas import ChatCaller
from app.services.ai.chat.streaming import CompletionStream
from tests.chat_proxy import lease, person, pools, SpyPool, UsageRows

CALLER = ChatCaller(user_id='ada', org_id='org-1')
STREAM = Mock(spec=CompletionStream)


class _Present:
    """A caller who stays."""

    async def unless_it_closes[T](self, work: Coroutine[Any, Any, T]) -> T | None:
        return await work


class _LeavesAt:
    """A caller who leaves at the *nth* piece of work (1: admission, 2: completion)."""

    def __init__(self, nth: int) -> None:
        self._nth = nth
        self.seen = 0

    async def unless_it_closes[T](self, work: Coroutine[Any, Any, T]) -> T | None:
        self.seen += 1
        if self.seen == self._nth:
            work.close()
            return None
        return await work


def _response() -> ChatCompletionResponse:
    return ChatCompletionResponse.model_validate(
        {
            'id': 'c-1',
            'object': 'chat.completion',
            'created': 0,
            'model': 'node-model',
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'hello'}, 'finish_reason': 'stop'}],
        }
    )


def _proxy(*, queue_lease=None):
    queue = SimpleNamespace(take=AsyncMock(return_value=queue_lease or lease(1, model='node-model')))
    streamer = SimpleNamespace(open=Mock(return_value=STREAM))
    completions = SimpleNamespace(complete=AsyncMock(return_value=_response()))
    proxy = ChatProxy(
        callers=person(),
        pools=pools(SpyPool()),
        queue_factory=lambda: queue,
        usage_db=UsageRows(),
        streamer=streamer,
        completions=completions,
    )
    return proxy, streamer, completions


def _body(*, stream: bool = False, model: str | None = None) -> ChatCompletionRequest:
    return ChatCompletionRequest(messages=[{'role': 'user', 'content': 'hi'}], stream=stream, model=model)


async def test_a_caller_who_leaves_while_admitted_has_left():
    proxy, streamer, completions = _proxy()

    answer = await proxy.answer(caller=CALLER, body=_body(), presence=_LeavesAt(1))

    assert answer == CallerLeft()
    streamer.open.assert_not_called()
    completions.complete.assert_not_called()


async def test_a_caller_who_leaves_while_it_is_generated_has_left():
    proxy, _, completions = _proxy()
    presence = _LeavesAt(2)

    answer = await proxy.answer(caller=CALLER, body=_body(), presence=presence)

    assert answer == CallerLeft()
    assert presence.seen == 2
    completions.complete.assert_called_once()


async def test_a_stream_is_answered_unstarted():
    proxy, streamer, completions = _proxy()

    answer = await proxy.answer(caller=CALLER, body=_body(stream=True), presence=_Present())

    assert isinstance(answer, StreamedAnswer)
    assert answer.stream is STREAM
    streamer.open.assert_called_once()
    completions.complete.assert_not_called()


async def test_a_completion_is_answered_whole():
    proxy, _, _ = _proxy()

    answer = await proxy.answer(caller=CALLER, body=_body(), presence=_Present())

    assert answer == CompletedAnswer(response=_response())


async def test_with_no_model_asked_for_the_nodes_model_is_sent():
    proxy, _, completions = _proxy()

    await proxy.answer(caller=CALLER, body=_body(), presence=_Present())

    (ctx,) = completions.complete.call_args.args
    assert ctx.request.model == 'node-model'


async def test_a_model_asked_for_is_the_one_sent():
    proxy, _, completions = _proxy()

    await proxy.answer(caller=CALLER, body=_body(model='asked-model'), presence=_Present())

    (ctx,) = completions.complete.call_args.args
    assert ctx.request.model == 'asked-model'
