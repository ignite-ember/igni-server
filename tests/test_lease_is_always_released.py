"""A node's concurrency slot comes back however the request ends.

`pool.acquire` takes a lease against an inference node's in-flight gauge,
and the release used to live inside `_log_usage` — which is only *reached*
on the happy path. A client that disconnected mid-stream returned from the
generator before it; a non-200 upstream raised before it.

Measured against a running API with a stub node, before the fix:

    1 cancelled stream   ->  1 lease held
    9 cancelled streams  ->  9 leases held
    a completed stream   ->  released correctly

Linear, and each lease is held for `DEFAULT_LEASE_TIMEOUT_SECONDS` = 900s
until the pool's staleness sweep prunes it. A node advertising 64 slots
therefore drops out of rotation after 64 cancellations inside 15 minutes,
and every user gets `429 Every inference node is at capacity`.

Cancelling a generation is the single most ordinary thing a user does in a
coding assistant.

The sweep is real, and the pool's own docstring says why leaning on it is
wrong: *"relying on the staleness sweep to clean up after each one would
mean the gauge is wrong for as long as the lease timeout, which is exactly
when scheduling matters."* It also offers a context-manager form it calls
"the one to reach for" — which this endpoint does not use, because it has
to hold the lease across a streaming response rather than a block.

So release moved into a `finally` on each path. After the fix, 12 cancelled
streams left 0 leases.

`aclose()` is used here to stand in for a client disconnect: that is what
Starlette does to the response generator when the connection drops.
"""

from __future__ import annotations

import inspect
import json
import pathlib
import re

import httpx
import pytest

from app.services.ai.chat import request_context
from app.services.ai.chat.completion import ChatCompletionService
from app.services.ai.chat.errors import UpstreamRefusal
from app.services.ai.chat.streaming import CompletionStream
from app.services.ai.upstream import UpstreamStale
from tests.chat_proxy import (
    completions,
    context,
    drain,
    NO_BACKOFF,
    on_transport,
    ScriptedStreams,
    SpyPool,
    sse,
    streamer,
)


def _frames(pool: SpyPool, upstream: ScriptedStreams):
    return streamer(upstream).open(context(pool)).frames()


class TestTheStreamingPath:
    async def test_a_completed_stream_releases(self):
        pool = SpyPool()

        await drain(_frames(pool, ScriptedStreams(([sse('a'), sse('b')], None))))

        assert pool.released == ['lease-1'], pool.released

    async def test_an_abandoned_stream_releases(self):
        """The finding. `aclose()` is what Starlette does to the generator
        when the client disconnects, and before the fix this path never
        reached the release at all."""
        pool = SpyPool()
        frames = _frames(pool, ScriptedStreams(([sse(f'tok{i}') for i in range(50)], None)))

        await frames.__anext__()  # one chunk, then walk away
        await frames.aclose()

        assert pool.released == ['lease-1'], (
            f'lease not returned after the client went away: {pool.released}. It would sit on the node for 900s.'
        )

    async def test_an_upstream_failure_releases(self):
        """Exhausting the retries yields an error chunk and finishes; the
        lease must not go with it."""
        pool = SpyPool()

        await drain(_frames(pool, ScriptedStreams(([], UpstreamStale()))))

        # Three attempts, so three leases are taken: the original plus one
        # per retry, each of which hands the failed node's slot back before
        # taking another. The property is that nothing is left outstanding.
        assert sorted(pool.released) == sorted(pool.acquired), f'acquired {pool.acquired}, released {pool.released}'
        assert len(pool.released) == len(set(pool.released)), f'a lease was released twice: {pool.released}'

    async def test_it_is_released_once_not_repeatedly(self):
        """A second release would be harmless — the pool removes a member
        from a sorted set — but a Redis round trip on every request is
        worth not paying."""
        pool = SpyPool()

        await drain(_frames(pool, ScriptedStreams(([sse('a')], None))))

        assert len(pool.released) == 1, pool.released


def _replying(status_code: int, text: str):
    return on_transport(lambda _request: httpx.Response(status_code, text=text))


class TestTheNonStreamingPath:
    async def test_a_successful_call_releases(self):
        pool = SpyPool()

        await completions(_replying(200, json.dumps({'id': 'x', 'choices': [], 'usage': {}}))).complete(context(pool))

        assert pool.released == ['lease-1']

    async def test_a_502_from_upstream_still_releases(self):
        """This branch raises before usage is logged, which is exactly why
        it used to leak. A 500 is retried on another node, so the
        invariant is that *every lease this request took came back*."""
        pool = SpyPool()

        with pytest.raises(UpstreamRefusal) as caught:
            await completions(_replying(500, 'upstream exploded')).complete(context(pool))

        assert caught.value.status_code == 502
        assert caught.value.detail == 'The inference node returned 500.'
        assert pool.released == pool.acquired, pool.released

    async def test_an_unparseable_200_is_502_not_500(self):
        """A 200 carrying something that is not JSON is the upstream's
        fault, not this server's."""
        pool = SpyPool()

        with pytest.raises(UpstreamRefusal) as caught:
            await completions(_replying(200, 'data: {"not":"json-at-the-top-level"}\n\n')).complete(context(pool))

        assert caught.value.status_code == 502, caught.value.status_code
        assert caught.value.detail == 'The inference node returned an unreadable response.'
        assert pool.released == pool.acquired, pool.released

    async def test_a_200_that_is_not_a_completion_is_502_not_500(self):
        """JSON, but a choice with no message. It raised `KeyError` and
        became a 500; it is an unreadable answer like any other."""
        pool = SpyPool()

        with pytest.raises(UpstreamRefusal) as caught:
            await completions(_replying(200, json.dumps({'choices': [{'index': 0}]}))).complete(context(pool))

        assert caught.value.status_code == 502
        assert pool.released == pool.acquired, pool.released


class TestANodeThatDoesNotAnswer:
    """A registered node that is simply gone: `httpx.ConnectError`.

    Not a generic exception: the client catches `httpx.RequestError`, and
    a test that raised something else would pass against a bare
    `except Exception` too. A dead node must be retried elsewhere and,
    failing that, answered with a 502 — a 500 blames this server.
    """

    @staticmethod
    def _refusing(failures: int) -> tuple[dict, object]:
        state = {'attempts': 0}

        def node(_request: httpx.Request) -> httpx.Response:
            state['attempts'] += 1
            if state['attempts'] <= failures:
                raise httpx.ConnectError('All connection attempts failed')
            return httpx.Response(200, json={'id': 'x', 'choices': [], 'usage': {}})

        return state, on_transport(node)

    async def test_it_retries_on_another_node(self):
        state, upstream = self._refusing(failures=1)
        pool = SpyPool()

        result = await completions(upstream).complete(context(pool))

        assert state['attempts'] == 2, 'it did not try a second node'
        assert result.id == 'x'
        # A second lease was taken, which is what "another node" means.
        assert pool.acquired == ['lease-1', 'lease-2'], pool.acquired

    async def test_the_dead_node_gets_its_slot_back_before_another_is_taken(self):
        _state, upstream = self._refusing(failures=1)
        pool = SpyPool()

        await completions(upstream).complete(context(pool))

        # 'lease-1' released before the retry, then the winning lease
        # released by the `finally`.
        assert pool.released == ['lease-1', 'lease-2'], pool.released

    async def test_when_nothing_answers_it_is_502_not_500(self):
        _state, upstream = self._refusing(failures=99)

        with pytest.raises(UpstreamRefusal) as caught:
            await completions(upstream).complete(context(SpyPool()))

        assert caught.value.status_code == 502, caught.value.status_code
        assert caught.value.detail == 'No inference node answered. Try again shortly.'

    async def test_it_gives_up_rather_than_retrying_forever(self):
        """Bounded by the retry policy both paths share."""
        state, upstream = self._refusing(failures=99)

        with pytest.raises(UpstreamRefusal):
            await completions(upstream).complete(context(SpyPool()))

        assert state['attempts'] == NO_BACKOFF.max_retries, state['attempts']

    async def test_the_lease_is_still_released_when_nothing_answers(self):
        _state, upstream = self._refusing(failures=99)
        pool = SpyPool()

        with pytest.raises(UpstreamRefusal):
            await completions(upstream).complete(context(pool))

        assert pool.released == pool.acquired, 'a node that never answered kept its slot for 900s'

    async def test_a_timeout_counts_as_not_answering(self):
        """A node that accepts the connection and then says nothing — a
        wedged vLLM — is a 504, told apart from a dead host's 502."""

        def wedged(_request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout('timed out')

        with pytest.raises(UpstreamRefusal) as caught:
            await completions(on_transport(wedged)).complete(context(SpyPool()))

        assert caught.value.status_code == 504
        assert caught.value.detail == 'The inference node stopped responding partway through.'


class TestTheResponsibilityStaysSplit:
    def test_log_usage_does_not_release(self):
        """Usage logging is only reached when there is usage to write;
        releasing there would restore the leak on every other path."""
        module_text = pathlib.Path(request_context.__file__).read_text()
        match = re.search(r'    async def log_usage\(.*?\n(?=    (?:async )?def |\n\S)', module_text, re.S)
        assert match, 'could not locate ChatRequestContext.log_usage in the module source'

        assert 'pool.release(' not in match.group(0), (
            'release is back inside log_usage, which is only reached on the '
            'happy path — the disconnect and upstream-error paths would leak again'
        )

    @pytest.mark.parametrize('method', [ChatCompletionService.complete, CompletionStream._finish])
    def test_each_path_releases_in_a_finally(self, method):
        source = inspect.getsource(method)

        assert 'finally:' in source, f'{method.__qualname__} has no finally'
        assert 'ctx.release()' in source, f'{method.__qualname__} does not release the lease'
