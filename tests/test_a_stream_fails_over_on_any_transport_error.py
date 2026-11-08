"""A streamed request whose node fails at the transport level is retried, then told why.

The stream used to catch only `ReadTimeout` among `httpx`'s errors: a
connect timeout, a pool timeout or a connection reset mid-answer escaped
the generator after the 200 had gone, and the client saw the connection
cut with no error frame and no second node asked. Every transport error
is an `UpstreamFailure` now, so the stream behaves as the non-streamed
path does. These drive the real stream over the real upstream client.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
import json

import httpx

from app.api.base_v1.schema.chat import ChatMessage
from app.services.ai.token_estimate import TokenEstimator
from app.services.ai.upstream import RetryPolicy, UpstreamFailure
from tests.chat_proxy import context, drain, on_transport, request, ScriptedStreams, SpyPool, sse, streamer, UsageRows


def _error(frames: list[str]) -> dict:
    (frame,) = [frame for frame in frames if frame.startswith('data: ') and '"error"' in frame]
    return json.loads(frame[6:])['error']


#: What the first node sent before its connection reset.
_LOST = 'several words the first node sent before it went away, '


class _ThenResets(httpx.AsyncByteStream):
    """A body that sends one chunk of the answer, then the connection resets."""

    def __init__(self, first: str) -> None:
        self._first = first

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield f'{self._first}\n\n'.encode()
        raise httpx.RemoteProtocolError('peer closed connection without sending complete message body')


async def _frames(handler, *, usage: UsageRows | None = None, pool: SpyPool | None = None) -> list[str]:
    ctx = context(pool or SpyPool(), usage_db=usage, upstream_request=request(stream=True))
    return await drain(streamer(on_transport(handler)).open(ctx).frames())


class TestATimeoutBeforeAnyAnswer:
    async def test_is_tried_on_three_nodes_then_ends_in_a_504_frame(self):
        nodes: list[str] = []

        def timing_out(sent: httpx.Request) -> httpx.Response:
            nodes.append(sent.url.host)
            raise httpx.ConnectTimeout('timed out')

        pool = SpyPool()
        frames = await _frames(timing_out, pool=pool)

        assert frames.count(': retry\n\n') == 2
        assert len(nodes) == 3
        assert _error(frames) == {
            'message': 'The inference node stopped responding partway through.',
            'type': 'server_error',
            'code': None,
            'param': None,
        }
        assert sorted(pool.released) == sorted(pool.acquired)

    async def test_a_pool_timeout_is_one_too(self):
        def pool_timeout(_sent: httpx.Request) -> httpx.Response:
            raise httpx.PoolTimeout('no connection free')

        frames = await _frames(pool_timeout)

        assert _error(frames)['message'] == 'The inference node stopped responding partway through.'


class TestAResetPartwayThrough:
    async def test_starts_the_answer_over_on_another_node_and_bills_only_that_one(self):
        """What the client already read stays read; the retry comment marks
        where the second node's answer begins, and only that is billed."""
        calls = {'n': 0}

        def resets_once(_sent: httpx.Request) -> httpx.Response:
            calls['n'] += 1
            if calls['n'] == 1:
                return httpx.Response(200, stream=_ThenResets(sse(_LOST)))
            return httpx.Response(200, text=f'{sse("kept")}\n\ndata: [DONE]\n\n')

        usage = UsageRows()
        frames = await _frames(resets_once, usage=usage)

        contents = [
            json.loads(frame[6:])['choices'][0]['delta']['content'] if frame.startswith('data: {') else frame
            for frame in frames
        ]
        assert contents == [_LOST, ': retry\n\n', 'kept', 'data: [DONE]\n\n']
        (row,) = usage.rows
        assert row['output_tokens'] == _estimate(['kept']) < _estimate([_LOST, 'kept'])

    async def test_on_every_node_ends_in_a_502_frame(self):
        def always_resets(_sent: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=_ThenResets(sse('part ')))

        frames = await _frames(always_resets)

        assert frames.count(': retry\n\n') == 2
        assert _error(frames)['message'] == 'No inference node answered. Try again shortly.'


def _estimate(pieces: list[str]) -> int:
    return (
        TokenEstimator()
        .estimate(messages=[ChatMessage(role='user', content='hi')], output_pieces=pieces)
        .completion_tokens
    )


class _PoolWithNoOtherNode(SpyPool):
    async def acquire(self, user_id: str, *, model: str | None = None, exclude_backend_id: str | None = None):
        raise RuntimeError('every node is taken')


class TestNoOtherNodeToTake:
    async def test_the_retry_goes_to_the_same_node_and_says_so(self, caplog):
        upstream = ScriptedStreams(
            ([], UpstreamFailure.from_transport(httpx.ConnectError('refused'))), ([sse('ok'), 'data: [DONE]'], None)
        )
        pool = _PoolWithNoOtherNode()

        frames = await drain(streamer(upstream).open(context(pool)).frames())

        assert [held.lease_id for held in upstream.calls] == ['lease-1', 'lease-1']
        assert ': retry\n\n' in frames
        assert frames[-1] == 'data: [DONE]\n\n'
        assert 'Could not take another node after node-1 failed; retrying on it: every node is taken' in caplog.text


class TestThePauseBetweenAttempts:
    async def test_grows_with_each_attempt(self, monkeypatch):
        pauses: list[float] = []

        async def no_wait(seconds: float) -> None:
            pauses.append(seconds)

        monkeypatch.setattr('app.services.ai.upstream.asyncio.sleep', no_wait)
        upstream = ScriptedStreams(([], UpstreamFailure.from_transport(httpx.ConnectError('refused'))))

        await drain(streamer(upstream, retry=RetryPolicy(max_retries=3, backoff=0.5)).open(context(SpyPool())).frames())

        assert pauses == [0.5, 1.0]
