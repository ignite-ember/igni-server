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
Starlette does to the response generator when the connection drops, and it
exercises the same `finally` without waiting out the endpoint's 2-second
disconnect poll.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.base_v1.endpoints.client import chat as chat_module


@dataclass
class _SpyPool:
    """Counts releases, and records which lease each one was for."""

    released: list[str] = field(default_factory=list)

    async def release(self, lease):
        self.released.append(getattr(lease, 'lease_id', str(lease)))

    acquired: list[str] = field(default_factory=lambda: ['lease-1'])

    async def acquire(self, *args, **kwargs):
        """A retry hands back the failed node's slot and takes another, so
        a realistic spy must issue a *new* lease rather than refuse."""
        nxt = f'lease-{len(self.acquired) + 1}'
        self.acquired.append(nxt)
        return _FakeLease(lease_id=nxt)

    report_usage = None


@dataclass
class _FakeLease:
    lease_id: str = 'lease-1'
    backend_id: str = 'node-1'
    base_url: str = 'http://node.invalid'
    model: str = 'fake-model'
    api_key: str | None = None


def _ctx(pool: _SpyPool):
    """A real ``_RequestContext`` over a spy pool.

    It used to be a ``MagicMock`` with the five fields set on it, which
    worked while releasing and logging were module-level functions that
    merely *took* a context. They are methods on it now — the lease and
    the pool are what they drive — so a mock would be standing in for
    the very code under test. The pool is still a spy; the context is
    the real thing.
    """
    return chat_module._RequestContext(
        user_id='user-1',
        org_id='org-1',
        model='fake-model',
        lease=_FakeLease(),
        pool=pool,
    )


def _sse(content: str) -> str:
    return 'data: ' + json.dumps(
        {
            'id': 'c',
            'object': 'chat.completion.chunk',
            'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': None}],
        }
    )


@pytest.fixture(autouse=True)
def _no_usage_write(monkeypatch):
    """Usage persistence is not what these tests are about.

    Patched on the context class: logging usage and releasing the lease
    are both methods of ``_RequestContext`` now, which is the thing that
    owns the lease and the pool they drive.
    """
    monkeypatch.setattr(chat_module._RequestContext, 'log_usage', AsyncMock())


@pytest.fixture
def upstream(monkeypatch):
    """Replace the upstream line iterator with a scripted one."""

    def _install(lines: list[str], raise_after: Exception | None = None):
        async def fake_stream(url, headers, body):
            for line in lines:
                yield line, None
            if raise_after is not None:
                raise raise_after

        monkeypatch.setattr(chat_module, '_stream_upstream', fake_stream)

    return _install


class TestTheStreamingPath:
    async def test_a_completed_stream_releases(self, upstream):
        upstream([_sse('a'), _sse('b')])
        pool = _SpyPool()
        ctx = _ctx(pool)

        response = await chat_module._stream_response('http://node.invalid/chat/completions', {}, {}, ctx, MagicMock())
        async for _ in response.body_iterator:
            pass

        assert pool.released == ['lease-1'], pool.released

    async def test_an_abandoned_stream_releases(self, upstream):
        """The finding. `aclose()` is what Starlette does to the generator
        when the client disconnects, and before the fix this path never
        reached the release at all."""
        upstream([_sse(f'tok{i}') for i in range(50)])
        pool = _SpyPool()
        ctx = _ctx(pool)

        response = await chat_module._stream_response('http://node.invalid/chat/completions', {}, {}, ctx, MagicMock())
        agen = response.body_iterator
        await agen.__anext__()  # one chunk, then walk away
        await agen.aclose()

        assert pool.released == [
            'lease-1'
        ], f'lease not returned after the client went away: {pool.released}. It would sit on the node for 900s.'

    async def test_an_upstream_failure_releases(self, upstream):
        """Exhausting the retries yields an error chunk and finishes; the
        lease must not go with it."""
        upstream([], raise_after=chat_module._UpstreamStale())
        pool = _SpyPool()
        ctx = _ctx(pool)

        response = await chat_module._stream_response('http://node.invalid/chat/completions', {}, {}, ctx, MagicMock())
        async for _ in response.body_iterator:
            pass

        # Three attempts, so three leases are taken: the original plus one
        # per retry, each of which hands the failed node's slot back before
        # taking another. The property is that nothing is left outstanding.
        assert sorted(pool.released) == sorted(pool.acquired), f'acquired {pool.acquired}, released {pool.released}'
        assert len(pool.released) == len(set(pool.released)), f'a lease was released twice: {pool.released}'

    async def test_it_is_released_once_not_repeatedly(self, upstream):
        """`finally` plus the old release site would have double-released.
        Harmless — the pool removes a member from a sorted set — but a
        second Redis round trip on every request is worth not paying."""
        upstream([_sse('a')])
        pool = _SpyPool()
        ctx = _ctx(pool)

        response = await chat_module._stream_response('http://node.invalid/chat/completions', {}, {}, ctx, MagicMock())
        async for _ in response.body_iterator:
            pass

        assert len(pool.released) == 1, pool.released


class TestTheNonStreamingPath:
    @pytest.fixture
    def http(self, monkeypatch):
        """Scripted upstream response for the non-streaming path."""

        def _install(status_code: int, body: str):
            class _Resp:
                def __init__(self):
                    self.status_code = status_code
                    self.text = body

                def json(self):
                    return json.loads(body)

            class _Client:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                async def post(self, *a, **kw):
                    return _Resp()

            monkeypatch.setattr(chat_module.httpx, 'AsyncClient', lambda **kw: _Client())

        return _install

    async def test_a_successful_call_releases(self, http):
        http(200, json.dumps({'id': 'x', 'choices': [], 'usage': {}}))
        pool = _SpyPool()
        ctx = _ctx(pool)

        await chat_module._non_stream_response('http://node.invalid', {}, {}, ctx)

        assert pool.released == ['lease-1']

    async def test_a_502_from_upstream_still_releases(self, http):
        """This branch raises before usage is logged, which is exactly why
        it used to leak.

        A 500 is now retried on another node, so the assertion is no
        longer "one lease" — it is *every lease this request took came
        back*, which is the invariant the leak broke and the stronger
        statement. A retry loop that releases two of three is the same
        bug wearing a hat.
        """
        from fastapi import HTTPException

        http(500, 'upstream exploded')
        pool = _SpyPool()
        ctx = _ctx(pool)

        with pytest.raises(HTTPException) as caught:
            await chat_module._non_stream_response('http://node.invalid', {}, {}, ctx)

        assert caught.value.status_code == 502
        assert pool.released == pool.acquired, pool.released

    async def test_an_unparseable_200_is_502_not_500(self, http):
        """A 200 carrying something that is not JSON used to raise
        JSONDecodeError and surface as a bare 500 — blaming this server for
        the upstream's reply. Found with a stub node that streamed SSE to a
        non-streaming request; a real node behind a misconfigured proxy does
        the same.
        """
        from fastapi import HTTPException

        http(200, 'data: {"not":"json-at-the-top-level"}\n\n')
        pool = _SpyPool()
        ctx = _ctx(pool)

        with pytest.raises(HTTPException) as caught:
            await chat_module._non_stream_response('http://node.invalid', {}, {}, ctx)

        assert caught.value.status_code == 502, caught.value.status_code
        assert pool.released == pool.acquired, pool.released


class TestANodeThatDoesNotAnswer:
    """A registered node that is simply gone.

    The streaming path has retried on another node since it was written.
    This one had neither the retry nor any handling of a transport
    failure, so `httpx.ConnectError` escaped the handler and FastAPI
    answered **500**.

    Found by registering a node pointed at an address nothing was
    listening on — which is what a spot reclaim, a crash, or a mistyped
    `base_url` looks like, and the registry keeps a dead node for up to
    its 180-second TTL (F114). A 500 also blames this server for the
    upstream being absent and trips `IgniServerErrors` (F101) for a
    fault that is not ours. F125.
    """

    @pytest.fixture
    def http(self, monkeypatch):
        """An upstream that refuses the first *n* attempts.

        `httpx.ConnectError` specifically, not a generic exception: the
        fix catches `httpx.RequestError`, and a test that raised
        something else would pass against a bare `except Exception`
        too.
        """

        def _install(failures: int, body: str | None = None):
            state = {'attempts': 0}

            class _Resp:
                status_code = 200

                def __init__(self):
                    self.text = body or json.dumps({'id': 'x', 'choices': [], 'usage': {}})

                def json(self):
                    return json.loads(self.text)

            class _Client:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                async def post(self, *a, **kw):
                    state['attempts'] += 1
                    if state['attempts'] <= failures:
                        raise chat_module.httpx.ConnectError('All connection attempts failed')
                    return _Resp()

            monkeypatch.setattr(chat_module.httpx, 'AsyncClient', lambda **kw: _Client())
            return state

        return _install

    async def test_it_retries_on_another_node(self, http):
        """The behaviour the streaming path already had."""
        state = http(failures=1)
        pool = _SpyPool()
        ctx = _ctx(pool)

        result = await chat_module._non_stream_response('http://node.invalid', {}, {}, ctx)

        assert state['attempts'] == 2, 'it did not try a second node'
        assert result is not None
        # A second lease was taken, which is what "another node" means.
        assert pool.acquired == ['lease-1', 'lease-2'], pool.acquired

    async def test_the_dead_node_gets_its_slot_back_before_another_is_taken(self, http):
        """Or a retry loop inflates the dead node's gauge — the same
        reason the streaming path releases first."""
        http(failures=1)
        pool = _SpyPool()
        ctx = _ctx(pool)

        await chat_module._non_stream_response('http://node.invalid', {}, {}, ctx)

        # 'lease-1' released before the retry, then the winning lease
        # released by the `finally`.
        assert pool.released[0] == 'lease-1', pool.released
        assert len(pool.released) == 2, pool.released

    async def test_when_nothing_answers_it_is_502_not_500(self, http):
        """The finding itself. A 500 says this server broke; a 502 says
        the thing behind it did not answer, which is true and which a
        caller can act on."""
        from fastapi import HTTPException

        http(failures=99)
        pool = _SpyPool()
        ctx = _ctx(pool)

        with pytest.raises(HTTPException) as caught:
            await chat_module._non_stream_response('http://node.invalid', {}, {}, ctx)

        assert caught.value.status_code == 502, caught.value.status_code
        assert 'answer' in str(caught.value.detail)

    async def test_it_gives_up_rather_than_retrying_forever(self, http):
        """Bounded by the same `_MAX_RETRIES` the streaming path uses. A
        fleet of twenty dead nodes must not become twenty upstream
        attempts on one request."""
        from fastapi import HTTPException

        state = http(failures=99)
        pool = _SpyPool()
        ctx = _ctx(pool)

        with pytest.raises(HTTPException):
            await chat_module._non_stream_response('http://node.invalid', {}, {}, ctx)

        assert state['attempts'] == chat_module._MAX_RETRIES, state['attempts']

    async def test_the_lease_is_still_released_when_nothing_answers(self, http):
        """This file's own subject: every exit path hands the slot back."""
        from fastapi import HTTPException

        http(failures=99)
        pool = _SpyPool()
        ctx = _ctx(pool)

        with pytest.raises(HTTPException):
            await chat_module._non_stream_response('http://node.invalid', {}, {}, ctx)

        assert pool.released, 'a node that never answered kept its slot for 900s'

    async def test_a_timeout_counts_as_not_answering(self, monkeypatch):
        """`ReadTimeout` is a `RequestError` too, and a node that accepts
        the connection and then says nothing is the more common failure
        of the two — a wedged vLLM rather than a dead host."""
        from fastapi import HTTPException

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **kw):
                raise chat_module.httpx.ReadTimeout('timed out')

        monkeypatch.setattr(chat_module.httpx, 'AsyncClient', lambda **kw: _Client())
        pool = _SpyPool()
        ctx = _ctx(pool)

        with pytest.raises(HTTPException) as caught:
            await chat_module._non_stream_response('http://node.invalid', {}, {}, ctx)

        # 504, not 502, and the distinction is the point of the test's
        # own docstring: this node *accepted* the connection and then
        # went quiet, where a dead host never answered at all. Both are
        # 5xx and both trip the same alert; the caller gets told which
        # one happened. What must never come back is the bare 500.
        assert caught.value.status_code == 504
        assert caught.value.status_code != 500


class TestTheResponsibilityStaysSplit:
    def test_log_usage_no_longer_releases(self):
        """It moved out on purpose. Putting it back would restore the leak,
        because usage logging is only reached when the request succeeded.

        Read from the file rather than via ``inspect.getsource``: the
        autouse fixture in this module replaces the method with an
        AsyncMock, and ``getsource`` on that raises TypeError.

        Both live on ``_RequestContext`` now, so this reads the one
        method rather than a module-level function — up to the next
        ``def`` at method indentation, or the end of the class.
        """
        import pathlib
        import re

        module_text = pathlib.Path(chat_module.__file__).read_text()
        match = re.search(r'    async def log_usage\(.*?\n(?=    (?:async )?def |\n\S)', module_text, re.S)
        assert match, 'could not locate _RequestContext.log_usage in the module source'
        source = match.group(0)

        # The *call*, not the word. The docstring explains at length that
        # releasing moved out, so it mentions "release" repeatedly — the
        # first version of this assertion failed on that prose.
        assert 'pool.release(' not in source, (
            'release is back inside log_usage, which is only reached on the '
            'happy path — the disconnect and upstream-error paths would leak again'
        )

    @pytest.mark.parametrize('func', ['_stream_response', '_non_stream_response'])
    def test_each_path_releases_in_a_finally(self, func):
        import inspect

        source = inspect.getsource(getattr(chat_module, func))

        assert 'finally:' in source, f'{func} has no finally'
        assert 'ctx.release()' in source, f'{func} does not release the lease'
