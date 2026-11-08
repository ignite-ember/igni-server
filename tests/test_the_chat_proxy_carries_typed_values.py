"""The values the chat proxy carries: what goes upstream, what comes back, and what is billed.

The body sent to a node, its answer and a stream's running usage were
dicts and a dataclass; they are models now, and these pin the behaviour
the dicts had — the bytes sent, the defaults filled, the usage counted.
"""

from __future__ import annotations

from app.api.base_v1.schema.chat import ChatCompletionRequest, CompletionUsage
from app.services.ai.chat.schemas import (
    StreamChunk,
    StreamUsage,
    UpstreamChatRequest,
    UpstreamCompletion,
    UpstreamLine,
)
from app.services.ai.token_estimate import TokenEstimator
from tests.chat_proxy import context, lease, request, SpyPool, UsageRows


class TestTheBodySentUpstream:
    def test_extras_orphans_and_a_callers_stream_options(self):
        """Extras pass through, an orphaned tool result becomes a system
        note, a truncated tool call is closed, and the caller's own
        `stream_options` wins over ours."""
        body = ChatCompletionRequest.model_validate(
            {
                'model': 'asked-for',
                'stream': True,
                'stream_options': {'include_usage': False},
                'seed': 7,
                'messages': [
                    {
                        'role': 'assistant',
                        'content': None,
                        'tool_calls': [{'id': 'c1', 'type': 'function', 'function': {'arguments': '{"a": "b"'}}],
                    },
                    {'role': 'user', 'content': 'between'},
                    {'role': 'tool', 'tool_call_id': 'c1', 'content': 'late result'},
                ],
            }
        )

        wire = UpstreamChatRequest.for_upstream(body, model='served').wire()

        assert wire == {
            'model': 'served',
            'messages': [
                {
                    'role': 'assistant',
                    'tool_calls': [{'id': 'c1', 'type': 'function', 'function': {'arguments': '{"a": "b"}'}}],
                },
                {'role': 'user', 'content': 'between'},
                {'role': 'system', 'content': '[Tool result]: late result'},
            ],
            'stream': True,
            'stream_options': {'include_usage': False},
            'seed': 7,
        }

    def test_the_inbound_bounds_do_not_apply_to_what_is_sent(self):
        """A repair may leave a shape the inbound check would refuse —
        here, every message dropped — and that is the node's to judge."""
        body = ChatCompletionRequest(messages=[{'role': 'tool', 'tool_call_id': 'x', 'content': ''}])

        assert UpstreamChatRequest.for_upstream(body, model='m').wire() == {
            'model': 'm',
            'messages': [],
            'stream': False,
        }


class TestSwitchingNodes:
    def test_the_model_sent_and_the_model_billed_move_together(self):
        usage = UsageRows()
        ctx = context(SpyPool(), usage_db=usage, upstream_request=request(model='first'))

        ctx.switch_to(lease(2, model='second'))

        assert (ctx.lease.lease_id, ctx.model, ctx.request.model) == ('lease-2', 'second', 'second')

    async def test_usage_is_billed_to_the_model_that_served_it(self):
        usage = UsageRows()
        ctx = context(SpyPool(), usage_db=usage, upstream_request=request(model='first'))
        ctx.switch_to(lease(2, model='second'))

        await ctx.log_usage(
            CompletionUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3), duration=0.5, stream=True
        )

        assert usage.rows == [
            {
                'user_id': 'user-1',
                'org_id': 'org-1',
                'app_id': None,
                'model': 'second',
                'input_tokens': 1,
                'output_tokens': 2,
                'total_tokens': 3,
                'duration': 0.5,
                'stream': True,
            }
        ]


class TestANonStreamedAnswer:
    def test_missing_fields_take_their_defaults(self):
        """A choice with no index takes its position; an empty usage is none; null content is ''."""
        completion = UpstreamCompletion.model_validate(
            {'choices': [{'message': {'role': 'assistant', 'content': None}}, {'index': 5, 'message': {'role': 'a'}}]}
        )

        response = completion.to_response()

        assert response.model_dump() == {
            'id': '',
            'object': 'chat.completion',
            'created': 0,
            'model': '',
            'choices': [
                {
                    'index': 0,
                    'message': {
                        'role': 'assistant',
                        'content': '',
                        'reasoning_content': None,
                        'name': None,
                        'tool_calls': None,
                        'tool_call_id': None,
                    },
                    'finish_reason': None,
                },
                {
                    'index': 5,
                    'message': {
                        'role': 'a',
                        'content': '',
                        'reasoning_content': None,
                        'name': None,
                        'tool_calls': None,
                        'tool_call_id': None,
                    },
                    'finish_reason': None,
                },
            ],
            'usage': None,
        }

    def test_a_reported_usage_is_kept(self):
        completion = UpstreamCompletion.model_validate({'usage': {'prompt_tokens': 2, 'total_tokens': 2}})

        assert completion.to_response().usage == CompletionUsage(prompt_tokens=2, completion_tokens=0, total_tokens=2)


class TestAStreamsUsage:
    def _chunk(self, line: str) -> StreamChunk:
        parsed = UpstreamLine.from_sse(line)
        assert parsed is not None and parsed.chunk is not None
        return parsed.chunk

    def test_it_takes_the_nodes_count(self):
        used = StreamUsage()
        used.absorb(self._chunk('data: {"choices": [{"delta": {"content": "hi"}}]}'))
        used.absorb(
            self._chunk(
                'data: {"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5}}'
            )
        )

        assert used.output_pieces == ['hi']
        assert used.settle(estimator=TokenEstimator(), request=request()) == CompletionUsage(
            prompt_tokens=4, completion_tokens=1, total_tokens=5
        )

    def test_an_empty_usage_block_does_not_erase_a_count(self):
        used = StreamUsage()
        used.absorb(self._chunk('data: {"usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5}}'))
        used.absorb(self._chunk('data: {"choices": [], "usage": {}}'))

        assert used.reported == CompletionUsage(prompt_tokens=4, completion_tokens=1, total_tokens=5)

    def test_without_a_count_it_estimates(self):
        used = StreamUsage()
        used.absorb(self._chunk('data: {"choices": [{"delta": {"content": "hello there"}}]}'))
        prompt = request([{'role': 'user', 'content': 'Say hello'}])

        settled = used.settle(estimator=TokenEstimator(), request=prompt)

        assert settled == TokenEstimator().estimate(messages=prompt.messages, output_pieces=['hello there'])
        assert settled.completion_tokens > 0

    def test_a_retry_forgets_the_failed_attempt(self):
        used = StreamUsage()
        used.absorb(self._chunk('data: {"choices": [{"delta": {"content": "lost"}}], "usage": {"total_tokens": 9}}'))

        used.start_over()

        assert (used.output_pieces, used.reported) == ([], None)

    def test_an_odd_chunk_is_forwarded_without_being_counted(self):
        parsed = UpstreamLine.from_sse('data: {"choices": [{"delta": {"content": ["a", "list"]}}]}')

        assert parsed is not None
        assert parsed.chunk is None
        assert parsed.frame == 'data: {"choices": [{"delta": {"content": ["a", "list"]}}]}\n\n'
