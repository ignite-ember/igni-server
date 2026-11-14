"""Tests for streaming chat usage extraction — provider usage and tiktoken fallback."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.api.base_v1.schema.chat import ChatCompletionRequest, ChatMessage
from app.services.ai.chat.schemas import UpstreamChatRequest
from app.services.ai.chat.upstream_client import UpstreamChatClient
from app.services.ai.provider_quirks import TruncatedJson, UpstreamConversation
from app.services.ai.providers.credentials import CloudCredentials
from app.services.ai.upstream import UpstreamError, UpstreamFailure, UpstreamStale
from tests.chat_proxy import context, drain, lease, request, ScriptedStreams, SpyPool, streamer, UsageRows

# ── Helpers to build SSE lines ────────────────────────────────────────


def _sse_chunk(
    content: str = '',
    usage: dict | None = None,
    finish_reason: str | None = None,
    reasoning_content: str | None = None,
):
    """Build a single SSE data line mimicking the OpenAI streaming format."""
    delta: dict = {}
    if content:
        delta['content'] = content
    if reasoning_content is not None:
        delta['content'] = delta.get('content')  # explicit null like the real API
        delta['reasoning_content'] = reasoning_content
    if not delta:
        delta = {}
    chunk = {
        'id': 'chatcmpl-test',
        'object': 'chat.completion.chunk',
        'choices': [
            {
                'index': 0,
                'delta': delta,
                'finish_reason': finish_reason,
            }
        ],
    }
    if usage is not None:
        chunk['usage'] = usage
    return f'data: {json.dumps(chunk)}'


def _sse_done():
    return 'data: [DONE]'


def _gave_up(collected: list[str]) -> bool:
    """Whether the stream ended by telling the client it had failed.

    Asked by parsing the frame rather than matching a substring. These
    assertions used to look for the literal ``"upstream_error"``, which
    quietly stopped describing the endpoint when the frame moved to the
    OpenAI envelope — a passing test for a string nothing emitted.
    """
    for line in collected:
        if not line.startswith('data: '):
            continue
        try:
            frame = json.loads(line[6:])
        except ValueError:
            continue
        if isinstance(frame, dict) and 'error' in frame:
            return True
    return False


# ── Fake httpx stream response ────────────────────────────────────────


class _FakeStreamResponse:
    """Mimics httpx.Response with async line iterator."""

    def __init__(self, lines: list[str], status_code: int = 200):
        self.status_code = status_code
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b'error'

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class _FakeHttpClient:
    """Mimics httpx.AsyncClient with stream context manager."""

    def __init__(self, response: _FakeStreamResponse):
        self._response = response

    def stream(self, *args, **kwargs):
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


# ── Tests ─────────────────────────────────────────────────────────────


class TestStreamUsageExtraction:
    """Usage as the real stream records it: the node's own count, or an estimate."""

    async def test_usage_from_provider(self):
        """When the provider sends usage in the final chunk, use those values."""
        lines = [
            _sse_chunk(content='Hello'),
            _sse_chunk(content=' world'),
            _sse_chunk(
                finish_reason='stop',
                usage={'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
            ),
            _sse_done(),
        ]
        usage = await self._run_stream(lines)

        assert usage['input_tokens'] == 10
        assert usage['output_tokens'] == 5
        assert usage['total_tokens'] == 15

    async def test_usage_null_falls_back_to_tiktoken(self):
        """When usage is explicitly null, tiktoken fallback kicks in."""
        lines = [
            _sse_chunk(content='Hello'),
            _sse_chunk(content=' world'),
            _sse_chunk(finish_reason='stop', usage=None),
            _sse_done(),
        ]
        usage = await self._run_stream(lines, messages=[{'role': 'user', 'content': 'Say hello'}])

        assert usage['total_tokens'] > 0
        assert usage['input_tokens'] > 0
        assert usage['output_tokens'] > 0

    async def test_usage_missing_falls_back_to_tiktoken(self):
        """When no usage field at all, tiktoken fallback kicks in."""
        lines = [
            _sse_chunk(content='Hi'),
            _sse_chunk(content=' there'),
            _sse_done(),
        ]
        usage = await self._run_stream(lines, messages=[{'role': 'user', 'content': 'Hello'}])

        assert usage['total_tokens'] > 0
        assert usage['input_tokens'] > 0
        assert usage['output_tokens'] > 0

    async def test_no_crash_on_malformed_chunks(self):
        """Malformed JSON in SSE lines should not crash."""
        lines = [
            'data: {invalid json',
            'data: ',
            _sse_chunk(content='OK'),
            _sse_done(),
        ]
        usage = await self._run_stream(lines, messages=[{'role': 'user', 'content': 'test'}])

        # Should still get tiktoken estimate from the valid chunk
        assert usage['output_tokens'] > 0

    async def test_empty_stream(self):
        """Empty stream with no chunks should not crash."""
        lines = [_sse_done()]
        usage = await self._run_stream(lines, messages=[])

        assert usage['total_tokens'] == 0

    async def test_provider_usage_preferred_over_tiktoken(self):
        """Provider usage should be used even if tiktoken would give different numbers."""
        lines = [
            _sse_chunk(content='A very long response with many tokens'),
            _sse_chunk(
                finish_reason='stop',
                usage={'prompt_tokens': 100, 'completion_tokens': 200, 'total_tokens': 300},
            ),
            _sse_done(),
        ]
        usage = await self._run_stream(lines)

        # Should use provider values, not tiktoken estimate
        assert usage['total_tokens'] == 300
        assert usage['input_tokens'] == 100
        assert usage['output_tokens'] == 200

    async def test_reasoning_content_preserved_separately(self):
        """reasoning_content should stay separate from content."""
        lines = [
            _sse_chunk(reasoning_content='Let me think...'),
            _sse_chunk(reasoning_content='The user wants hello'),
            _sse_chunk(content='Hello!'),
            _sse_done(),
        ]
        result = await self._run_stream(lines)

        # reasoning_content chunks should keep reasoning_content field
        rc_values = []
        for chunk in result['chunks']:
            for choice in chunk.get('choices', []):
                rc = choice.get('delta', {}).get('reasoning_content')
                if rc:
                    rc_values.append(rc)
        assert rc_values == ['Let me think...', 'The user wants hello']

        # content should only have the real content
        contents = [
            choice.get('delta', {}).get('content')
            for chunk in result['chunks']
            for choice in chunk.get('choices', [])
            if choice.get('delta', {}).get('content')
        ]
        assert 'Hello!' in contents

    async def test_null_content_normalised_to_empty_string(self):
        """content: null should become content: '' so clients don't break."""
        lines = [
            _sse_chunk(reasoning_content='thinking'),
            _sse_done(),
        ]
        result = await self._run_stream(lines)

        for chunk in result['chunks']:
            for choice in chunk.get('choices', []):
                delta = choice.get('delta', {})
                # content should be '' not None
                assert delta.get('content') is not None

    async def test_reasoning_model_full_flow(self):
        """Simulate a full reasoning model response: think → respond → usage."""
        lines = [
            _sse_chunk(reasoning_content='User wants a greeting'),
            _sse_chunk(reasoning_content='. Simple response.'),
            _sse_chunk(content='Hi there!'),
            _sse_chunk(
                finish_reason='stop',
                usage={'prompt_tokens': 20, 'completion_tokens': 30, 'total_tokens': 50},
            ),
            _sse_done(),
        ]
        result = await self._run_stream(lines)

        assert result['total_tokens'] == 50
        assert result['input_tokens'] == 20
        assert result['output_tokens'] == 30

    async def test_keepalive_comments_ignored(self):
        """SSE comment lines (: keepalive) should not break parsing."""
        lines = [
            _sse_chunk(content='Hello'),
            ': keepalive',
            ': keepalive',
            _sse_chunk(content=' world'),
            _sse_done(),
        ]
        result = await self._run_stream(lines)

        contents = [
            choice.get('delta', {}).get('content')
            for chunk in result['chunks']
            for choice in chunk.get('choices', [])
            if choice.get('delta', {}).get('content')
        ]
        assert contents == ['Hello', ' world']

    def test_stream_options_injected(self):
        """A stream asks the node for usage."""
        body = ChatCompletionRequest(model='test', messages=[{'role': 'user', 'content': 'hi'}], stream=True)

        wire = UpstreamChatRequest.for_upstream(body, model='test-model').wire()

        assert wire['stream_options'] == {'include_usage': True}
        assert wire['model'] == 'test-model'

    def test_stream_options_not_overwritten(self):
        """A caller's own stream_options is passed through as sent."""
        body = ChatCompletionRequest(
            model='test',
            messages=[{'role': 'user', 'content': 'hi'}],
            stream=True,
            stream_options={'include_usage': False},
        )

        wire = UpstreamChatRequest.for_upstream(body, model='test-model').wire()

        assert wire['stream_options'] == {'include_usage': False}

    def test_a_request_that_does_not_stream_asks_for_no_stream_options(self):
        body = ChatCompletionRequest(messages=[{'role': 'user', 'content': 'hi'}])

        assert 'stream_options' not in UpstreamChatRequest.for_upstream(body, model='m').wire()

    # ── Helper ────────────────────────────────────────────────────────

    async def _run_stream(
        self,
        lines: list[str],
        messages: list[dict] | None = None,
    ) -> dict:
        """Run *lines* through the real stream; the usage row it wrote, and the chunks it forwarded."""
        usage = UsageRows()
        ctx = context(
            SpyPool(),
            usage_db=usage,
            upstream_request=UpstreamChatRequest(
                model='test-model',
                messages=messages if messages is not None else [{'role': 'user', 'content': 'hi'}],
                stream=True,
            ),
        )
        frames = await drain(streamer(ScriptedStreams((lines, None))).open(ctx).frames())
        chunks = [chunk for chunk in map(_parsed, frames) if chunk is not None]
        (row,) = usage.rows
        return {**row, 'chunks': chunks}


# ── Tests for upstream retry logic ───────────────────────────────────


class TestStreamUpstreamRetry:
    """The upstream client raises on a non-200, and the stream retries what another node could fix."""

    async def test_upstream_error_raises_not_yields(self):
        """Non-200 from upstream raises UpstreamError rather than yielding an error chunk."""
        with pytest.raises(UpstreamError) as exc_info:
            await drain(_streamed(_FakeStreamResponse([], status_code=500)))
        assert exc_info.value.status_code == 500

    async def test_lines_are_forwarded_normalised_with_their_chunk(self):
        """A data line's frame is the normalised JSON; comments and [DONE] pass through as sent."""
        lines = await drain(
            _streamed(_FakeStreamResponse([_sse_chunk(reasoning_content='hm'), ': keepalive', '', _sse_done()]))
        )

        assert [line.frame for line in lines[1:]] == [': keepalive\n\n', 'data: [DONE]\n\n']
        assert json.loads(lines[0].frame[6:])['choices'][0]['delta'] == {'content': '', 'reasoning_content': 'hm'}
        assert lines[0].chunk is not None
        assert [line.chunk for line in lines[1:]] == [None, None]

    async def test_a_line_that_is_not_json_passes_through_without_a_chunk(self):
        (line,) = await drain(_streamed(_FakeStreamResponse(['data: {invalid json'])))

        assert (line.frame, line.chunk) == ('data: {invalid json\n\n', None)

    async def test_a_transport_error_is_an_upstream_failure(self):
        """So the stream fails over on it, whatever kind it is."""

        def refusing(**_kwargs):
            raise httpx.ConnectError('refused')

        client = UpstreamChatClient(credentials=CloudCredentials(), client_factory=refusing)

        with pytest.raises(UpstreamFailure) as exc_info:
            await drain(client.stream(lease=lease(), request=request(stream=True)))
        assert exc_info.value.label == 'connect_error'

    async def test_retry_succeeds_after_upstream_500(self):
        """Simulate: first attempt gets 500, second attempt succeeds.
        Client should see a retry comment then the successful content."""
        collected = await self._run_retry_loop(
            attempts_responses=[
                _FailResponse(500),
                _SuccessResponse(
                    [
                        _sse_chunk(content='Hello'),
                        _sse_done(),
                    ]
                ),
            ],
        )

        # Should have a retry comment, then the successful content
        assert ': retry\n\n' in collected
        assert any('Hello' in line for line in collected)
        # Should NOT contain an error data chunk
        assert not _gave_up(collected)

    async def test_retry_succeeds_on_third_attempt(self):
        """Two 500s then success on third attempt."""
        collected = await self._run_retry_loop(
            attempts_responses=[
                _FailResponse(500),
                _FailResponse(502),
                _SuccessResponse(
                    [
                        _sse_chunk(content='Finally'),
                        _sse_done(),
                    ]
                ),
            ],
        )

        retry_count = sum(1 for line in collected if line == ': retry\n\n')
        assert retry_count == 2
        assert any('Finally' in line for line in collected)
        assert not _gave_up(collected)

    async def test_all_retries_exhausted_yields_error(self):
        """Three 500s should yield an error to the client on the final attempt."""
        collected = await self._run_retry_loop(
            attempts_responses=[
                _FailResponse(500),
                _FailResponse(500),
                _FailResponse(500),
            ],
        )

        retry_count = sum(1 for line in collected if line == ': retry\n\n')
        assert retry_count == 2  # retries before attempts 2 and 3
        assert _gave_up(collected)

    async def test_retry_resets_output_pieces(self):
        """After a retry, output_pieces should be cleared so token
        estimation uses only the successful attempt's content."""
        collected = await self._run_retry_loop(
            attempts_responses=[
                _FailResponse(500),
                _SuccessResponse(
                    [
                        _sse_chunk(content='Good'),
                        _sse_chunk(
                            finish_reason='stop',
                            usage={'prompt_tokens': 5, 'completion_tokens': 3, 'total_tokens': 8},
                        ),
                        _sse_done(),
                    ]
                ),
            ],
        )

        # The successful attempt's content should be present
        assert any('Good' in line for line in collected)

    @pytest.mark.parametrize('code', [429, 502, 503])
    async def test_upstream_error_various_status_codes(self, code):
        """UpstreamError carries the node's status code."""
        with pytest.raises(UpstreamError) as exc_info:
            await drain(_streamed(_FakeStreamResponse([], status_code=code)))
        assert exc_info.value.status_code == code

    async def test_mixed_error_types_in_retry(self):
        """First attempt _UpstreamError (500), second _UpstreamStale, third succeeds."""
        collected = await self._run_retry_loop(
            attempts_responses=[
                _FailResponse(500),
                _StaleResponse(),
                _SuccessResponse(
                    [
                        _sse_chunk(content='Recovered'),
                        _sse_done(),
                    ]
                ),
            ],
        )

        retry_count = sum(1 for line in collected if line == ': retry\n\n')
        assert retry_count == 2
        assert any('Recovered' in line for line in collected)
        assert not _gave_up(collected)

    async def test_all_stale_retries_exhausted(self):
        """Three stale timeouts should yield an error on the final attempt."""
        collected = await self._run_retry_loop(
            attempts_responses=[
                _StaleResponse(),
                _StaleResponse(),
                _StaleResponse(),
            ],
        )

        retry_count = sum(1 for line in collected if line == ': retry\n\n')
        assert retry_count == 2
        assert _gave_up(collected)

    async def test_upstream_error_preserves_body(self):
        """UpstreamError keeps the response body for logging."""
        error_body = b'{"detail":"Rate limit exceeded"}'
        fake_resp = _FakeStreamResponse([], status_code=429)
        fake_resp.aread = AsyncMock(return_value=error_body)

        with pytest.raises(UpstreamError) as exc_info:
            await drain(_streamed(fake_resp))
        assert exc_info.value.body == error_body
        assert exc_info.value.status_code == 429

    async def test_connect_error_retries(self):
        """A node that refuses the connection is tried again elsewhere, as a timeout is."""
        collected = await self._run_retry_loop(
            attempts_responses=[
                _ConnectErrorResponse(),
                _SuccessResponse([_sse_chunk(content='Recovered'), _sse_done()]),
            ],
        )

        assert ': retry\n\n' in collected
        assert any('Recovered' in line for line in collected)
        assert not _gave_up(collected)

    async def test_read_timeout_retries(self):
        """ReadTimeout (Nebius hung before headers) should be retried."""
        collected = await self._run_retry_loop(
            attempts_responses=[
                _ReadTimeoutResponse(),
                _SuccessResponse(
                    [
                        _sse_chunk(content='Recovered'),
                        _sse_done(),
                    ]
                ),
            ],
        )

        assert ': retry\n\n' in collected
        assert any('Recovered' in line for line in collected)
        assert not _gave_up(collected)

    async def test_read_timeout_all_retries_exhausted(self):
        """Three ReadTimeouts should yield an error on the final attempt."""
        collected = await self._run_retry_loop(
            attempts_responses=[
                _ReadTimeoutResponse(),
                _ReadTimeoutResponse(),
                _ReadTimeoutResponse(),
            ],
        )

        retry_count = sum(1 for line in collected if line == ': retry\n\n')
        assert retry_count == 2
        assert _gave_up(collected)

    async def test_read_timeout_then_500_then_success(self):
        """Mixed: ReadTimeout, then 500, then success."""
        collected = await self._run_retry_loop(
            attempts_responses=[
                _ReadTimeoutResponse(),
                _FailResponse(500),
                _SuccessResponse(
                    [
                        _sse_chunk(content='Finally'),
                        _sse_done(),
                    ]
                ),
            ],
        )

        retry_count = sum(1 for line in collected if line == ': retry\n\n')
        assert retry_count == 2
        assert any('Finally' in line for line in collected)

    # ── Helpers ────────────────────────────────────────────────────────

    async def _run_retry_loop(self, attempts_responses: list) -> list[str]:
        """Drive the real stream over nodes that answer each attempt as scripted; the frames sent."""
        upstream = ScriptedStreams(*(spec.attempt() for spec in attempts_responses))
        return await drain(streamer(upstream).open(context(SpyPool())).frames())


class _FailResponse:
    """An upstream non-200 response."""

    def __init__(self, status_code: int):
        self.status_code = status_code

    def attempt(self):
        return [], UpstreamError(self.status_code, b'error')


class _StaleResponse:
    """An upstream that went silent (stale timeout)."""

    def attempt(self):
        return [], UpstreamStale('No data from upstream')


class _ReadTimeoutResponse:
    """An upstream that never sent headers (connection hung)."""

    def attempt(self):
        return [], UpstreamFailure.from_transport(httpx.ReadTimeout('timed out'))


class _ConnectErrorResponse:
    """An upstream that refused the connection."""

    def attempt(self):
        return [], UpstreamFailure.from_transport(httpx.ConnectError('refused'))


class _SuccessResponse:
    """A successful upstream response with SSE lines."""

    def __init__(self, lines: list[str]):
        self.lines = lines

    def attempt(self):
        return self.lines, None


def _parsed(frame: str) -> dict | None:
    """The JSON a forwarded data frame carries, or None."""
    try:
        return json.loads(frame.removeprefix('data: '))
    except ValueError:
        return None


def _streamed(response: _FakeStreamResponse):
    """The upstream client's lines over one scripted `httpx` stream response."""
    client = UpstreamChatClient(
        credentials=CloudCredentials(), client_factory=lambda **_kwargs: _FakeHttpClient(response)
    )
    return client.stream(lease=lease(), request=request(stream=True))


# ── Tests for tool call argument sanitisation ────────────────────────


def _arguments_repaired(body: dict) -> dict:
    """*body* with its messages repaired as they would be on the way upstream."""
    conversation = UpstreamConversation(messages=[ChatMessage.model_validate(m) for m in body['messages']])
    return {'messages': [m.model_dump(exclude_none=True) for m in conversation.repaired()]}


class TestRepairJsonString:
    """`TruncatedJson.closed` handles all cases without breaking valid input."""

    def test_valid_json_unchanged(self):
        valid = '{"path": "/tmp/test.txt"}'
        assert TruncatedJson(value=valid).closed() == valid

    def test_valid_complex_json_unchanged(self):
        valid = json.dumps(
            {'file_path': 'x.py', 'old_string': 'def foo():\n    pass\n', 'new_string': 'def bar():\n    pass\n'}
        )
        assert TruncatedJson(value=valid).closed() == valid

    def test_truncated_missing_closing_brace(self):
        broken = '{"file_path": "x.py", "old_string": "some code"'
        result = TruncatedJson(value=broken).closed()
        parsed = json.loads(result)
        assert parsed['file_path'] == 'x.py'
        assert parsed['old_string'] == 'some code'

    def test_truncated_mid_value(self):
        broken = '{"file_path": "x.py", "old_string": "def foo():\\n    return True'
        result = TruncatedJson(value=broken).closed()
        parsed = json.loads(result)
        assert parsed['file_path'] == 'x.py'
        assert 'foo' in parsed['old_string']

    def test_valid_with_escaped_quotes(self):
        valid = json.dumps({'code': 'he said "hello"'})
        assert TruncatedJson(value=valid).closed() == valid

    def test_valid_with_newlines(self):
        valid = json.dumps({'code': 'line1\nline2\nline3'})
        assert TruncatedJson(value=valid).closed() == valid

    def test_valid_with_unicode(self):
        valid = json.dumps({'text': 'hello — world • émoji'})
        assert TruncatedJson(value=valid).closed() == valid

    def test_empty_object_valid(self):
        assert TruncatedJson(value='{}').closed() == '{}'

    def test_simple_args_valid(self):
        valid = '{"path": "."}'
        assert TruncatedJson(value=valid).closed() == valid

    def test_kwargs_wrapped_valid(self):
        """Double-encoded kwargs — valid JSON, should not be modified."""
        valid = json.dumps({'kwargs': json.dumps({'path': '.'})})
        assert TruncatedJson(value=valid).closed() == valid


class TestSanitiseToolCallArguments:
    """`UpstreamConversation` fixes broken args without touching valid ones."""

    def test_valid_messages_unchanged(self):
        body = {
            'messages': [
                {'role': 'user', 'content': 'hi'},
                {
                    'role': 'assistant',
                    'content': None,
                    'tool_calls': [
                        {
                            'id': 'c1',
                            'type': 'function',
                            'function': {'name': 'read_file', 'arguments': '{"path": "."}'},
                        }
                    ],
                },
                {'role': 'tool', 'tool_call_id': 'c1', 'content': 'file1.txt'},
            ]
        }
        original_args = body['messages'][1]['tool_calls'][0]['function']['arguments']
        body = _arguments_repaired(body)
        assert body['messages'][1]['tool_calls'][0]['function']['arguments'] == original_args

    def test_broken_arguments_repaired(self):
        body = {
            'messages': [
                {
                    'role': 'assistant',
                    'content': None,
                    'tool_calls': [
                        {
                            'id': 'c1',
                            'type': 'function',
                            'function': {
                                'name': 'edit_file',
                                'arguments': '{"file_path": "x.py", "old_string": "code here"',
                            },
                        }
                    ],
                },
            ]
        }
        body = _arguments_repaired(body)
        fixed = body['messages'][0]['tool_calls'][0]['function']['arguments']
        parsed = json.loads(fixed)
        assert parsed['file_path'] == 'x.py'

    def test_messages_without_tool_calls_untouched(self):
        body = {
            'messages': [
                {'role': 'user', 'content': 'hello'},
                {'role': 'assistant', 'content': 'hi there'},
            ]
        }
        body = _arguments_repaired(body)
        assert body['messages'][0]['content'] == 'hello'
        assert body['messages'][1]['content'] == 'hi there'

    def test_multiple_tool_calls_mixed(self):
        """One valid, one broken — only the broken one gets fixed."""
        valid_args = '{"path": "."}'
        broken_args = '{"file_path": "x.py", "old_string": "truncated'
        body = {
            'messages': [
                {
                    'role': 'assistant',
                    'content': None,
                    'tool_calls': [
                        {'id': 'c1', 'type': 'function', 'function': {'name': 'list_files', 'arguments': valid_args}},
                        {'id': 'c2', 'type': 'function', 'function': {'name': 'edit_file', 'arguments': broken_args}},
                    ],
                },
            ]
        }
        body = _arguments_repaired(body)
        # Valid one untouched
        assert body['messages'][0]['tool_calls'][0]['function']['arguments'] == valid_args
        # Broken one repaired
        fixed = body['messages'][0]['tool_calls'][1]['function']['arguments']
        assert json.loads(fixed)  # parses without error

    def test_real_production_failure(self):
        """Reproduce the exact truncated edit_file arguments from production.

        The model generates a valid JSON object but it gets truncated before
        the closing brace — e.g. the new_string field and } are missing.
        """
        # Build a valid arguments dict, serialize, then chop off the end
        full_args = json.dumps(
            {
                'file_path': 'crud.py',
                'old_string': 'def create():\n    pass\n\ndef delete():\n    return True',
                'new_string': 'def create_item():\n    pass\n\ndef remove_item():\n    return True',
            }
        )
        # Truncate — remove new_string and closing brace
        cut_point = full_args.index(', "new_string"')
        broken_args = full_args[:cut_point]

        body = {
            'messages': [
                {
                    'role': 'assistant',
                    'content': 'Fixing...',
                    'tool_calls': [
                        {
                            'id': 'call_abc',
                            'type': 'function',
                            'function': {'name': 'edit_file', 'arguments': broken_args},
                        }
                    ],
                },
            ]
        }

        # Verify it's actually broken
        with pytest.raises(json.JSONDecodeError):
            json.loads(broken_args)

        body = _arguments_repaired(body)
        fixed = body['messages'][0]['tool_calls'][0]['function']['arguments']
        parsed = json.loads(fixed)
        assert parsed['file_path'] == 'crud.py'
        assert 'create' in parsed['old_string']
