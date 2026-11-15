"""A non-streamed answer waits `AI__UPSTREAM_TIMEOUT` once, not once per retry.

The setting is "how long the API waits for a model to answer", and the
compose file's 330 s stop grace is "the request budget plus headroom" over
its 300. A timed-out request was retried up to three times, on the same
node when it was the only one: measured on a stack with the timeout at
4 s, the caller waited 12.8 s and the node received three requests while
still busy with the first — past its concurrency limit.

A quick failure is still tried on another node; only the wait that has
already used the whole budget ends the request.
"""

from __future__ import annotations

import httpx
import pytest

from app.services.ai.chat.errors import UpstreamRefusal
from tests.chat_proxy import completions, context, on_transport, SpyPool

_GOOD = {
    'id': 'c-1',
    'object': 'chat.completion',
    'created': 1,
    'model': 'fake-model',
    'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop'}],
    'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},
}

pytestmark = pytest.mark.anyio


async def test_a_read_timeout_ends_the_request_after_one_wait():
    sent: list[httpx.Request] = []

    def node(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        raise httpx.ReadTimeout('the node is still thinking', request=request)

    pool = SpyPool()
    with pytest.raises(UpstreamRefusal) as caught:
        await completions(on_transport(node)).complete(context(pool))

    assert len(sent) == 1, f'the node was asked {len(sent)} times while still busy with the first'
    assert caught.value.status_code == 504
    assert pool.acquired == ['lease-1'], 'no second node was taken for it'


async def test_a_quick_failure_is_still_tried_on_another_node():
    answers = iter([httpx.Response(503, text='warming up'), httpx.Response(200, json=_GOOD)])

    response = await completions(on_transport(lambda _r: next(answers))).complete(context(SpyPool()))

    assert response.choices[0].message.content == 'ok'


async def test_a_connect_timeout_is_still_tried_on_another_node():
    """Nothing was sent: the node never took the request, so another may."""
    answers = iter(['connect', 'ok'])

    def node(request: httpx.Request) -> httpx.Response:
        if next(answers) == 'connect':
            raise httpx.ConnectTimeout('no route', request=request)
        return httpx.Response(200, json=_GOOD)

    response = await completions(on_transport(node)).complete(context(SpyPool()))

    assert response.choices[0].message.content == 'ok'
