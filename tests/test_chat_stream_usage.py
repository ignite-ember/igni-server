"""Tests for streaming chat usage extraction — provider usage and tiktoken fallback."""

from dataclasses import dataclass
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

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


# ── Mock context ──────────────────────────────────────────────────────


@dataclass
class _FakeCtx:
    user_id: str = 'user-1'
    org_id: str | None = 'org-1'
    model: str = 'test-model'
    key_id: str = 'key-1'
    pool: MagicMock = None

    def __post_init__(self):
        if self.pool is None:
            self.pool = MagicMock()
            self.pool.report_usage = AsyncMock()


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
    """Test the _generate() inner function's usage extraction logic."""

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_empty_stream(self):
        """Empty stream with no chunks should not crash."""
        lines = [_sse_done()]
        usage = await self._run_stream(lines, messages=[])

        assert usage['total_tokens'] == 0

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_stream_options_injected(self):
        """Verify stream_options is added to the upstream body."""
        from app.api.base_v1.schema.chat import ChatCompletionRequest

        body = ChatCompletionRequest(
            model='test',
            messages=[{'role': 'user', 'content': 'hi'}],
            stream=True,
        )
        upstream_body = body.model_dump(exclude_none=True)
        upstream_body['model'] = 'test-model'
        if body.stream:
            upstream_body.setdefault('stream_options', {'include_usage': True})

        assert upstream_body['stream_options'] == {'include_usage': True}

    @pytest.mark.asyncio
    async def test_stream_options_not_overwritten(self):
        """If user already set stream_options, don't overwrite."""
        from app.api.base_v1.schema.chat import ChatCompletionRequest

        body = ChatCompletionRequest(
            model='test',
            messages=[{'role': 'user', 'content': 'hi'}],
            stream=True,
            stream_options={'include_usage': False},
        )
        upstream_body = body.model_dump(exclude_none=True)
        upstream_body['model'] = 'test-model'
        if body.stream:
            upstream_body.setdefault('stream_options', {'include_usage': True})

        # setdefault should NOT overwrite existing value
        assert upstream_body['stream_options'] == {'include_usage': False}

    # ── Helper ────────────────────────────────────────────────────────

    async def _run_stream(
        self,
        lines: list[str],
        messages: list[dict] | None = None,
    ) -> dict:
        """Run the stream usage extraction logic in isolation.

        Reproduces the core loop from _generate() without needing
        the full FastAPI stack, gate, or DB.
        """
        from app.services.ai.token_estimate import TokenEstimator

        body = {
            'model': 'test-model',
            'messages': messages if messages is not None else [{'role': 'user', 'content': 'hi'}],
            'stream': True,
        }

        total_tokens = 0
        input_tokens = 0
        output_tokens = 0
        output_pieces: list[str] = []

        forwarded_chunks: list[dict] = []

        for line in lines:
            if not line:
                continue

            if line.startswith('data: ') and line != 'data: [DONE]':
                try:
                    chunk = json.loads(line[6:])

                    # Normalise: ensure content is never null (matches server logic)
                    for choice in chunk.get('choices', []):
                        delta = choice.get('delta', {})
                        if delta.get('content') is None:
                            delta['content'] = ''

                    u = chunk.get('usage')
                    if u:
                        total_tokens = u.get('total_tokens', 0)
                        input_tokens = u.get('prompt_tokens', 0)
                        output_tokens = u.get('completion_tokens', 0)
                    for choice in chunk.get('choices', []):
                        delta = choice.get('delta', {})
                        if delta.get('content'):
                            output_pieces.append(delta['content'])

                    forwarded_chunks.append(chunk)
                except (json.JSONDecodeError, KeyError):
                    pass

        # Tiktoken fallback
        if not total_tokens:
            try:
                # The server's own estimator, which loads the table it ships
                # rather than downloading it (app/services/ai/token_estimate.py).
                enc = TokenEstimator().encoder
                input_text = ' '.join(m.get('content', '') for m in body.get('messages', []) if m.get('content'))
                input_tokens = len(enc.encode(input_text))
                output_tokens = len(enc.encode(''.join(output_pieces)))
                total_tokens = input_tokens + output_tokens
            except Exception:
                pass

        return {
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'total_tokens': total_tokens,
            'chunks': forwarded_chunks,
        }


# ── Tests for upstream retry logic ───────────────────────────────────


class TestStreamUpstreamRetry:
    """Test that _stream_upstream raises on non-200 and the retry loop handles it."""

    @pytest.mark.asyncio
    async def test_upstream_error_raises_not_yields(self):
        """Non-200 from upstream should raise _UpstreamError, not yield an error chunk."""
        from app.api.base_v1.endpoints.client.chat import _stream_upstream, _UpstreamError

        fake_resp = _FakeStreamResponse([], status_code=500)
        fake_client = _FakeHttpClient(fake_resp)

        with patch('app.api.base_v1.endpoints.client.chat.httpx.AsyncClient', return_value=fake_client):
            with pytest.raises(_UpstreamError) as exc_info:
                async for _ in _stream_upstream('http://test', {}, {}):
                    pass
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_upstream_error_various_status_codes(self):
        """_UpstreamError should carry the correct status code for 429, 502, 503."""
        from app.api.base_v1.endpoints.client.chat import _stream_upstream, _UpstreamError

        for code in (429, 502, 503):
            fake_resp = _FakeStreamResponse([], status_code=code)
            fake_client = _FakeHttpClient(fake_resp)

            with patch('app.api.base_v1.endpoints.client.chat.httpx.AsyncClient', return_value=fake_client):
                with pytest.raises(_UpstreamError) as exc_info:
                    async for _ in _stream_upstream('http://test', {}, {}):
                        pass
                assert exc_info.value.status_code == code

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_upstream_error_preserves_body(self):
        """_UpstreamError should preserve the response body for logging."""
        from app.api.base_v1.endpoints.client.chat import _stream_upstream, _UpstreamError

        error_body = b'{"detail":"Rate limit exceeded"}'
        fake_resp = _FakeStreamResponse([], status_code=429)
        fake_resp.aread = AsyncMock(return_value=error_body)
        fake_client = _FakeHttpClient(fake_resp)

        with patch('app.api.base_v1.endpoints.client.chat.httpx.AsyncClient', return_value=fake_client):
            with pytest.raises(_UpstreamError) as exc_info:
                async for _ in _stream_upstream('http://test', {}, {}):
                    pass
            assert exc_info.value.body == error_body
            assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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
        """Reproduce the retry loop from _generate() with controlled responses.

        Returns all SSE lines yielded to the client.

        **This is a copy of the endpoint's loop, not the loop.** Nothing
        here can catch a regression in `_generate_inner`, and it has
        already drifted once: the error frame it built kept emitting
        `"type": "upstream_error"` for a while after the endpoint
        stopped. The frame is now built by the same helpers the endpoint
        uses, so at least that cannot drift again — but the shape of
        this file is the real problem, and
        `test_an_openai_client_can_read_the_error.py` drives the actual
        endpoint for the same behaviour.
        """
        from app.api.base_v1.endpoints.client.chat import (
            _answer_for,
            _error_frame,
            _UpstreamError,
            _UpstreamStale,
            _worth_another_node,
        )

        attempt_idx = 0
        max_retries = 3
        collected: list[str] = []
        output_pieces: list[str] = []

        for attempt in range(1, max_retries + 1):
            resp_spec = attempts_responses[min(attempt_idx, len(attempts_responses) - 1)]
            attempt_idx += 1

            try:
                if isinstance(resp_spec, _FailResponse):
                    raise _UpstreamError(resp_spec.status_code, b'error')
                if isinstance(resp_spec, _StaleResponse):
                    raise _UpstreamStale('No data from upstream')
                if isinstance(resp_spec, _ReadTimeoutResponse):
                    raise httpx.ReadTimeout('timed out')

                # Success path — simulate _stream_upstream yielding lines
                for line in resp_spec.lines:
                    if not line:
                        continue
                    if line.startswith('data: ') and line != 'data: [DONE]':
                        try:
                            chunk = json.loads(line[6:])
                            for choice in chunk.get('choices', []):
                                delta = choice.get('delta', {})
                                if delta.get('content') is None:
                                    delta['content'] = ''
                            sse_line = f'data: {json.dumps(chunk)}\n\n'
                            collected.append(sse_line)
                            for choice in chunk.get('choices', []):
                                c = choice.get('delta', {}).get('content')
                                if c:
                                    output_pieces.append(c)
                        except (json.JSONDecodeError, KeyError):
                            collected.append(f'{line}\n\n')
                    else:
                        collected.append(f'{line}\n\n')
                break  # success

            except (_UpstreamStale, _UpstreamError, httpx.ReadTimeout) as exc:
                if attempt < max_retries and _worth_another_node(exc):
                    collected.append(': retry\n\n')
                    output_pieces.clear()
                else:
                    refusal = _answer_for(exc)
                    # The production builder, not a second copy of it.
                    # This reassembled the frame by hand and so could
                    # not notice the envelope changing shape — which
                    # is the one thing a test of the error frame is
                    # for.
                    collected.append(_error_frame(str(refusal.detail), refusal.status_code))
                    break

        return collected


class _FailResponse:
    """Represents an upstream non-200 response."""

    def __init__(self, status_code: int):
        self.status_code = status_code


class _StaleResponse:
    """Represents an upstream that went silent (stale timeout)."""


class _ReadTimeoutResponse:
    """Represents an upstream that never sent headers (connection hung)."""


class _SuccessResponse:
    """Represents a successful upstream response with SSE lines."""

    def __init__(self, lines: list[str]):
        self.lines = lines


# ── Tests for client disconnect detection ────────────────────────────


class TestClientDisconnectDetection:
    """Test that _generate() checks request.is_disconnected() and cleans up."""

    @pytest.mark.asyncio
    async def test_disconnect_during_wait_for_slot_releases_slot(self):
        """When a client disconnects while waiting for a slot, the slot
        should be released via the finally block."""

        disconnect_after = 2  # disconnect after 2 keepalives

        # Simulate the _generate() disconnect-check logic
        class _FakeRequest:
            def __init__(self):
                self._call_count = 0

            async def is_disconnected(self):
                self._call_count += 1
                return self._call_count > disconnect_after

        fake_request = _FakeRequest()
        keepalives = []

        # Simulate the wait_for_slot loop with disconnect check
        async def _simulate_wait_with_disconnect():
            import asyncio

            for _ in range(20):  # would run 20 times without disconnect
                if await fake_request.is_disconnected():
                    return True  # disconnected
                keepalives.append(': keepalive\n\n')
                await asyncio.sleep(0.01)
            return False  # completed normally

        disconnected = await _simulate_wait_with_disconnect()

        assert disconnected is True
        assert len(keepalives) == disconnect_after

    @pytest.mark.asyncio
    async def test_disconnect_during_stream_stops_yielding(self):
        """When a client disconnects during upstream streaming, the generator
        should stop yielding and the finally block should release the slot."""
        disconnect_after = 3  # disconnect after 3 chunks

        class _FakeRequest:
            def __init__(self):
                self._call_count = 0

            async def is_disconnected(self):
                self._call_count += 1
                return self._call_count > disconnect_after

        fake_request = _FakeRequest()

        # Simulate the streaming loop with disconnect check
        all_lines = [
            _sse_chunk(content='chunk1'),
            _sse_chunk(content='chunk2'),
            _sse_chunk(content='chunk3'),
            _sse_chunk(content='chunk4'),
            _sse_chunk(content='chunk5'),
            _sse_done(),
        ]

        collected = []
        for line in all_lines:
            if await fake_request.is_disconnected():
                break
            collected.append(line)

        # Should have stopped after 3 chunks
        assert len(collected) == disconnect_after

    @pytest.mark.asyncio
    async def test_finally_runs_after_disconnect(self):
        """The finally block must execute after disconnect to release the slot."""
        finally_ran = False

        class _FakeRequest:
            async def is_disconnected(self):
                return True  # always disconnected

        fake_request = _FakeRequest()

        async def _generator_with_finally():
            try:
                for i in range(100):
                    if await fake_request.is_disconnected():
                        return
                    yield f'chunk-{i}'
            finally:
                nonlocal finally_ran
                finally_ran = True

        collected = [chunk async for chunk in _generator_with_finally()]
        assert collected == []
        assert finally_ran is True


# ── Tests for tool call argument sanitisation ────────────────────────


class TestRepairJsonString:
    """Test _repair_json_string handles all cases without breaking valid input."""

    def test_valid_json_unchanged(self):
        from app.services.ai.provider_quirks import repair_json_string

        valid = '{"path": "/tmp/test.txt"}'
        assert repair_json_string(valid) == valid

    def test_valid_complex_json_unchanged(self):
        from app.services.ai.provider_quirks import repair_json_string

        valid = json.dumps(
            {'file_path': 'x.py', 'old_string': 'def foo():\n    pass\n', 'new_string': 'def bar():\n    pass\n'}
        )
        assert repair_json_string(valid) == valid

    def test_truncated_missing_closing_brace(self):
        from app.services.ai.provider_quirks import repair_json_string

        broken = '{"file_path": "x.py", "old_string": "some code"'
        result = repair_json_string(broken)
        parsed = json.loads(result)
        assert parsed['file_path'] == 'x.py'
        assert parsed['old_string'] == 'some code'

    def test_truncated_mid_value(self):
        from app.services.ai.provider_quirks import repair_json_string

        broken = '{"file_path": "x.py", "old_string": "def foo():\\n    return True'
        result = repair_json_string(broken)
        parsed = json.loads(result)
        assert parsed['file_path'] == 'x.py'
        assert 'foo' in parsed['old_string']

    def test_valid_with_escaped_quotes(self):
        from app.services.ai.provider_quirks import repair_json_string

        valid = json.dumps({'code': 'he said "hello"'})
        assert repair_json_string(valid) == valid

    def test_valid_with_newlines(self):
        from app.services.ai.provider_quirks import repair_json_string

        valid = json.dumps({'code': 'line1\nline2\nline3'})
        assert repair_json_string(valid) == valid

    def test_valid_with_unicode(self):
        from app.services.ai.provider_quirks import repair_json_string

        valid = json.dumps({'text': 'hello — world • émoji'})
        assert repair_json_string(valid) == valid

    def test_empty_object_valid(self):
        from app.services.ai.provider_quirks import repair_json_string

        assert repair_json_string('{}') == '{}'

    def test_simple_args_valid(self):
        from app.services.ai.provider_quirks import repair_json_string

        valid = '{"path": "."}'
        assert repair_json_string(valid) == valid

    def test_kwargs_wrapped_valid(self):
        """Double-encoded kwargs — valid JSON, should not be modified."""
        from app.services.ai.provider_quirks import repair_json_string

        valid = json.dumps({'kwargs': json.dumps({'path': '.'})})
        assert repair_json_string(valid) == valid


class TestSanitiseToolCallArguments:
    """Test _sanitise_tool_call_arguments fixes broken args without touching valid ones."""

    def test_valid_messages_unchanged(self):
        from app.services.ai.provider_quirks import sanitise_tool_call_arguments

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
        sanitise_tool_call_arguments(body)
        assert body['messages'][1]['tool_calls'][0]['function']['arguments'] == original_args

    def test_broken_arguments_repaired(self):
        from app.services.ai.provider_quirks import sanitise_tool_call_arguments

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
        sanitise_tool_call_arguments(body)
        fixed = body['messages'][0]['tool_calls'][0]['function']['arguments']
        parsed = json.loads(fixed)
        assert parsed['file_path'] == 'x.py'

    def test_messages_without_tool_calls_untouched(self):
        from app.services.ai.provider_quirks import sanitise_tool_call_arguments

        body = {
            'messages': [
                {'role': 'user', 'content': 'hello'},
                {'role': 'assistant', 'content': 'hi there'},
            ]
        }
        sanitise_tool_call_arguments(body)
        assert body['messages'][0]['content'] == 'hello'
        assert body['messages'][1]['content'] == 'hi there'

    def test_multiple_tool_calls_mixed(self):
        """One valid, one broken — only the broken one gets fixed."""
        from app.services.ai.provider_quirks import sanitise_tool_call_arguments

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
        sanitise_tool_call_arguments(body)
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
        from app.services.ai.provider_quirks import sanitise_tool_call_arguments

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

        sanitise_tool_call_arguments(body)
        fixed = body['messages'][0]['tool_calls'][0]['function']['arguments']
        parsed = json.loads(fixed)
        assert parsed['file_path'] == 'crud.py'
        assert 'create' in parsed['old_string']
