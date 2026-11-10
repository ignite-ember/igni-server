"""A node's 200 that is JSON but not the answer asked for is unreadable, never this server's crash.

Before the chat proxy validated what a node sends, the non-streamed
path read the dict by hand: a body that was not an object, a `usage`
that was not one, or a null `id` escaped as a bare 500, and for a
broken choice the usage row had already been written for the attempt
that failed. The streamed path did the same per chunk: a `delta: null`
or a data line that was not an object raised mid-stream, after the 200.

Now a non-streamed answer that does not validate is `UpstreamUnreadable`
— retried on another node, then a 502 that says so — and a streamed
chunk that does not validate is forwarded as sent and left out of the
count. These drive the real upstream client over a mock transport.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.api.base_v1.schema.chat import ChatMessage
from app.services.ai.chat.errors import UpstreamRefusal
from app.services.ai.token_estimate import TokenEstimator
from tests.chat_proxy import completions, context, drain, on_transport, request, SpyPool, sse, streamer, UsageRows

_GOOD = {
    'id': 'good',
    'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'hi'}, 'finish_reason': 'stop'}],
    'usage': {'prompt_tokens': 3, 'completion_tokens': 1, 'total_tokens': 4},
}


def _replies(*bodies: str):
    """A node that answers 200 with each body in turn, repeating the last."""
    calls = {'n': 0}

    def node(_request: httpx.Request) -> httpx.Response:
        body = bodies[min(calls['n'], len(bodies) - 1)]
        calls['n'] += 1
        return httpx.Response(200, text=body)

    return on_transport(node)


class TestANonStreamedAnswerThatIsNotACompletion:
    @pytest.mark.parametrize(
        'body',
        [
            [1, 2],
            'just a string',
            {'id': None, 'choices': []},
            {'choices': [], 'usage': 'lots'},
            {'choices': [], 'usage': {'prompt_tokens': 'three'}},
            {'choices': [{'index': 0, 'message': 'hi'}]},
            {'choices': 'none'},
        ],
        ids=[
            'a-list',
            'a-string',
            'null-id',
            'usage-not-an-object',
            'usage-not-a-count',
            'message-a-string',
            'choices-not-a-list',
        ],
    )
    async def test_is_a_502_that_says_so(self, body):
        pool = SpyPool()

        with pytest.raises(UpstreamRefusal) as caught:
            await completions(_replies(json.dumps(body))).complete(context(pool))

        assert (caught.value.status_code, caught.value.detail) == (
            502,
            'The inference node returned an unreadable response.',
        )
        assert len(pool.acquired) == 3, pool.acquired
        assert sorted(pool.released) == sorted(pool.acquired)

    async def test_the_attempt_that_failed_writes_no_usage_row(self):
        """Only the node that answered is billed — the broken choice used
        to write its row before it crashed."""
        usage = UsageRows()
        broken = json.dumps({'id': 'half', 'choices': [{'index': 0, 'finish_reason': 'stop'}], 'usage': _GOOD['usage']})

        answer = await completions(_replies(broken, json.dumps(_GOOD))).complete(context(SpyPool(), usage_db=usage))

        assert answer.id == 'good'
        assert [(row['model'], row['total_tokens'], row['stream']) for row in usage.rows] == [('fake-model', 4, False)]

    async def test_a_choice_with_a_null_index_takes_its_position(self):
        """`"index": null` failed the response's own validation, a 500 after
        the row was written; each such choice is numbered by where it is."""
        body = {
            'id': 'x',
            'choices': [
                {'index': None, 'message': {'role': 'assistant', 'content': 'a'}},
                {'index': None, 'message': {'role': 'assistant', 'content': 'b'}},
            ],
        }

        answer = await completions(_replies(json.dumps(body))).complete(context(SpyPool()))

        assert [(choice.index, choice.message.content) for choice in answer.choices] == [(0, 'a'), (1, 'b')]


class TestAStreamedChunkThatIsNotAChunk:
    """Forwarded byte for byte, and not counted; the stream goes on."""

    _ODD = [
        'data: [1]',
        'data: null',
        'data: {"choices": [{"delta": null}]}',
        'data: {"choices": [{"delta": {"content": "dropped"}}], '
        '"usage": {"prompt_tokens": 3, "completion_tokens": null, "total_tokens": null}}',
    ]

    async def _stream(self, lines: list[str]) -> tuple[list[str], UsageRows]:
        body = ''.join(f'{line}\n\n' for line in lines)
        upstream = on_transport(lambda _request: httpx.Response(200, text=body))
        usage = UsageRows()
        frames = await drain(
            streamer(upstream).open(context(SpyPool(), usage_db=usage, upstream_request=request(stream=True))).frames()
        )
        return frames, usage

    async def test_is_forwarded_as_sent_and_the_stream_goes_on(self):
        frames, _usage = await self._stream([*self._ODD, sse('hello'), 'data: [DONE]'])

        assert frames[:4] == [
            'data: [1]\n\n',
            'data: null\n\n',
            'data: {"choices": [{"delta": null}]}\n\n',
            'data: {"choices": [{"delta": {"content": "dropped"}}], '
            '"usage": {"prompt_tokens": 3, "completion_tokens": null, "total_tokens": null}}\n\n',
        ]
        assert json.loads(frames[4][6:])['choices'][0]['delta']['content'] == 'hello'
        assert frames[5:] == ['data: [DONE]\n\n']
        assert ': retry\n\n' not in frames

    async def test_neither_its_text_nor_its_usage_is_counted(self):
        """A usage block with a null count is no report: the stream is
        estimated, from the text of the chunks that validated only."""
        _frames, usage = await self._stream([*self._ODD, sse('hello'), 'data: [DONE]'])

        expected = TokenEstimator().estimate(messages=[ChatMessage(role='user', content='hi')], output_pieces=['hello'])
        (row,) = usage.rows
        assert (row['input_tokens'], row['output_tokens'], row['total_tokens']) == (
            expected.prompt_tokens,
            expected.completion_tokens,
            expected.total_tokens,
        )
