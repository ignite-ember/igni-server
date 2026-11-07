"""Every refusal on `/chat/completions` arrives in a shape OpenAI SDKs read.

Measured before the fix, against a running deployment with no reachable
inference node. The server did everything right — retried, gave up, and
answered:

    502 {"detail": "No inference node answered. Try again shortly."}

and the user's screen said:

    No verdict. The model did not answer: Unknown model error

That string is `openai`'s own default. Its error path does
``e.response.json().get("error", {}).get("message", "Unknown model error")``
— there was no ``error`` key, so it took the default, raised a
`ModelProviderError` carrying it, and Agno put *that* where the agent's
answer goes. An accurate, actionable sentence was written by this server
and discarded one layer from the person who needed it.

The sentence is not the fix. The envelope is: a server that says it is
OpenAI-compatible has to be compatible when it is failing too, which is
precisely when the caller has nothing else to go on.

Both shapes are asserted — ``error.message`` for the SDK, ``detail`` for
everything that already reads it — because keeping the second is what
made the first a safe change.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
import httpx
import pytest

from app.api.base_v1.endpoints.client import chat as chat_module
from app.config import settings
from app.services.ai.backend_provider import BackendConfigError
from app.services.ai.schemas import (
    BackendLease,
    ModelNotAvailableError,
    NoBackendAvailableError,
)
from tests.conftest import auth_header, create_org, create_user, make_token

_CHAT = '/chat/completions'


def _body() -> dict:
    return {'model': 'some-model', 'messages': [{'role': 'user', 'content': 'hi'}]}


#: A minimal successful completion, for the node that answers after a
#: bad one.
_COMPLETION = {'id': 'done', 'object': 'chat.completion', 'choices': [], 'usage': {}}


class _HttpxStub:
    """Stands in for the `httpx` module, deferring whatever it does not
    override to the real one.

    The endpoint refers to httpx by module name, so a stub replaces the
    whole module — and then has to keep up with every attribute the
    endpoint happens to reference. It did not: adding one
    `isinstance(exc, httpx.TimeoutException)` broke three tests that
    had nothing to do with timeouts. Falling through means a stub only
    declares what it is actually faking.
    """

    def __getattr__(self, name):
        return getattr(httpx, name)


class _Reply:
    """One scripted upstream reply to a non-streaming request."""

    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)


class _ScriptedNodes(_HttpxStub):
    """``httpx``, as the endpoint sees it, answering a different way each
    time — which is the point: a retry must reach a *new* node."""

    def __init__(self, replies: list[_Reply]):
        self._replies = list(replies)
        self.calls = 0

    def AsyncClient(self, *_args, **_kwargs):  # noqa: N802 — a class to the caller
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, *_args, **_kwargs):
        reply = self._replies[min(self.calls, len(self._replies) - 1)]
        self.calls += 1
        return reply


class _FailingStream:
    """A node that answers a streaming request with a status and nothing
    else, every time — so every attempt fails and the frame is reached."""

    def __init__(self, status_code: int):
        self.status_code = status_code

    async def aread(self) -> bytes:
        return b'upstream said no'

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def aiter_lines(self):
        raise AssertionError('not reached: the status check comes first')


class _StreamingNodes(_HttpxStub):
    def __init__(self, resp):
        self._resp = resp

    def Timeout(self, **_kwargs):  # noqa: N802 — a class to the caller
        return None

    def AsyncClient(self, *_args, **_kwargs):  # noqa: N802
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def stream(self, *_args, **_kwargs):
        return self._resp


class _SpyPool:
    """Hands out a new lease each time, and counts."""

    def __init__(self):
        self.acquired = ['lease-1']
        self.released: list[str] = []

    async def release(self, lease):
        self.released.append(getattr(lease, 'lease_id', str(lease)))

    async def acquire(self, *_args, **_kwargs):
        nxt = f'lease-{len(self.acquired) + 1}'
        self.acquired.append(nxt)
        return BackendLease(
            lease_id=nxt,
            backend_id=f'node-{len(self.acquired)}',
            api_key=None,
            base_url='http://node.invalid/v1',
            model='m',
        )

    report_usage = None


def _spy_ctx(pool):
    """A real ``_RequestContext`` over a spy pool.

    Was a ``MagicMock``, which worked while releasing and logging were
    module-level functions that took a context. They are methods on it
    now, so mocking the context would mock the code under test.
    """
    return chat_module._RequestContext(
        user_id='u',
        org_id='o',
        model='m',
        lease=BackendLease(
            lease_id='lease-1',
            backend_id='node-1',
            api_key=None,
            base_url='http://node.invalid/v1',
            model='m',
        ),
        pool=pool,
    )


@pytest.fixture
def non_stream(monkeypatch):
    """A pool, a context, and a way to script what the nodes reply."""
    pool = _SpyPool()
    ctx = _spy_ctx(pool)

    def install(replies: list[_Reply]):
        monkeypatch.setattr(chat_module, 'httpx', _ScriptedNodes(replies))

    # Usage logging would reach the database; the lease release it also
    # performs is asserted by `test_lease_is_always_released`.
    # Both are methods of _RequestContext now — it owns the lease and
    # the pool they drive.
    monkeypatch.setattr(chat_module._RequestContext, 'log_usage', AsyncMock())
    monkeypatch.setattr(chat_module._RequestContext, 'release', AsyncMock())
    return pool, ctx, install


@pytest.fixture
def stream_ctx(monkeypatch):
    """A context whose upstream always fails the same way."""

    def _build(resp):
        pool = _SpyPool()
        ctx = _spy_ctx(pool)
        monkeypatch.setattr(chat_module, 'httpx', _StreamingNodes(resp))
        monkeypatch.setattr(chat_module._RequestContext, 'log_usage', AsyncMock())
        monkeypatch.setattr(chat_module._RequestContext, 'release', AsyncMock())
        return ctx

    return _build


class _DeadNode(_HttpxStub):
    """`httpx`, as the endpoint sees it, with nothing listening.

    Stands in for the module rather than for the class so the real
    exception types the endpoint catches are still the real ones — a
    stub that raised a lookalike would prove nothing about the
    `except httpx.RequestError` it is meant to exercise.
    """

    def AsyncClient(self, *_args, **_kwargs):  # noqa: N802 — it is a class to the caller
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, *_args, **_kwargs):
        raise httpx.ConnectError('connection refused')


@pytest.fixture
async def authed(client, db):
    await create_user(db, user_id='envelope-user', email='envelope@test.com')
    org = await create_org(db, owner_user_id='envelope-user', slug='envelope-org', display_name='E')
    return client, auth_header(make_token('envelope-user', org_id=str(org.id)))


def _pool(acquire_side_effect):
    pool = AsyncMock()
    pool.acquire = AsyncMock(side_effect=acquire_side_effect)
    return pool


def _with_pool(pool):
    return patch(
        'app.services.ai.pool_manager.PoolManager.get_pool',
        new=lambda _self: pool,
    )


def _read(response) -> tuple[str, str]:
    """``(message, type)`` out of the envelope, asserting it is there."""
    body = response.json()
    assert 'error' in body, f'no OpenAI error envelope: {body}'
    error = body['error']
    assert set(error) >= {'message', 'type', 'code', 'param'}, error
    return error['message'], error['type']


class TestTheReasonSurvivesToTheClient:
    """The case that was measured: the node is gone."""

    async def test_a_dead_node_says_so_where_an_sdk_looks(self, authed):
        client, headers = authed
        lease = BackendLease(
            lease_id='l1',
            backend_id='gpu-01',
            api_key=None,
            base_url='http://gone.invalid/v1',
            model='m',
        )
        pool = AsyncMock()
        pool.acquire = AsyncMock(return_value=lease)

        # Only the endpoint's own httpx is replaced. Patching
        # `httpx.AsyncClient.post` globally would unplug the test
        # client, which reaches the app over httpx too.
        with _with_pool(pool), patch.object(chat_module, 'httpx', _DeadNode()):
            response = await client.post(_CHAT, json=_body(), headers=headers)

        assert response.status_code == 502, response.text
        message, kind = _read(response)
        # The whole point: not "Unknown model error".
        assert 'inference node' in message, message
        assert kind == 'server_error'

    async def test_the_old_shape_is_still_there(self, authed):
        """`detail` is kept, so nothing that reads it had to change."""
        client, headers = authed
        pool = _pool(ModelNotAvailableError(model='nope', available=['m']))

        with _with_pool(pool):
            response = await client.post(_CHAT, json=_body(), headers=headers)

        assert response.status_code == 404
        assert response.json()['detail'] == response.json()['error']['message']


class TestEveryRefusalIsReadable:
    """Not just the one that was reported.

    Each of these is a different thing going wrong, and an SDK renders
    whichever it meets. One converted branch would have left the others
    saying "Unknown model error".
    """

    @pytest.mark.parametrize(
        ('failure', 'code', 'kind'),
        [
            (ModelNotAvailableError(model='nope', available=['m']), 404, 'invalid_request_error'),
            (BackendConfigError('No inference backends are available.'), 503, 'server_error'),
            (NoBackendAvailableError(), 429, 'rate_limit_error'),
        ],
    )
    async def test_the_acquire_failures(self, authed, failure, code, kind, monkeypatch):
        client, headers = authed
        # Every node full now waits its turn first (§19); not for long here.
        monkeypatch.setattr(settings, 'chat_queue_max_wait_seconds', 1)

        with _with_pool(_pool(failure)):
            response = await client.post(_CHAT, json=_body(), headers=headers)

        assert response.status_code == code, response.text
        message, reported = _read(response)
        assert message, 'an empty message is the bug with extra steps'
        assert reported == kind

    async def test_retry_after_survives_the_conversion(self, authed, monkeypatch):
        """A 429 that lost its header would turn a pause into a hot loop."""
        client, headers = authed
        monkeypatch.setattr(settings, 'chat_queue_max_wait_seconds', 1)

        with _with_pool(_pool(NoBackendAvailableError())):
            response = await client.post(_CHAT, json=_body(), headers=headers)

        assert response.status_code == 429
        assert response.headers.get('Retry-After') == '30'


class TestTheDoorIsReadableToo:
    """The error an SDK meets most often, and the one raised furthest
    from the handler: a token that is missing or no longer good.

    It comes from a dependency, not the endpoint body — which is why
    the conversion wraps the route rather than the function.
    """

    async def test_a_missing_token(self, client):
        response = await client.post(_CHAT, json=_body())

        assert response.status_code == 401
        message, kind = _read(response)
        assert message
        assert kind == 'authentication_error'

    async def test_a_token_that_is_not_one(self, client):
        response = await client.post(_CHAT, json=_body(), headers=auth_header('not-a-jwt'))

        assert response.status_code == 401
        _, kind = _read(response)
        assert kind == 'authentication_error'


class TestAStreamFailsInTheSameEnvelope:
    """A stream that dies mid-answer has already sent its 200, so its
    reason travels as an SSE frame rather than a status line.

    Different transport, same API — and it was not: the frame carried
    ``"type": "upstream_error"``, which is not a type any OpenAI SDK
    knows, alongside one generic message standing in for three
    different failures. These drive the real endpoint, not a copy of
    its retry loop.
    """

    async def _frames(self, ctx, body=None) -> list[str]:
        response = await chat_module._stream_response(
            'http://node.invalid/chat/completions', {}, body if body is not None else {}, ctx, MagicMock()
        )
        return [piece async for piece in response.body_iterator]

    def _error_frame(self, frames: list[str]) -> dict:
        for frame in frames:
            if frame.startswith('data: ') and '"error"' in frame:
                return json.loads(frame[6:])['error']
        raise AssertionError(f'no error frame in {frames}')

    async def test_the_type_is_one_an_sdk_knows(self, stream_ctx):
        ctx = stream_ctx(_FailingStream(503))

        error = self._error_frame(await self._frames(ctx))

        assert error['type'] == 'server_error', error
        assert set(error) >= {'message', 'type', 'code', 'param'}, error

    async def test_the_frame_says_which_failure_it_was(self, stream_ctx):
        """Not "Upstream AI provider returned an error" for everything."""
        ctx = stream_ctx(_FailingStream(503))

        error = self._error_frame(await self._frames(ctx))

        assert '503' in error['message'], error['message']

    async def test_a_refused_request_is_not_a_server_error(self, stream_ctx):
        """A 400 is about the request, and is typed as such."""
        ctx = stream_ctx(_FailingStream(400))

        error = self._error_frame(await self._frames(ctx))

        assert error['type'] == 'invalid_request_error', error

    async def test_a_4xx_is_not_tried_on_three_nodes(self, stream_ctx):
        """The retry comments are the visible cost of asking again."""
        ctx = stream_ctx(_FailingStream(400))

        frames = await self._frames(ctx)

        assert ': retry\n\n' not in frames, frames


class TestTheSentenceNamesTheFailure:
    """Three different things go wrong; three different sentences.

    `_answer_for` is where that is decided for both paths, so it is
    asserted directly rather than through either one.
    """

    def test_a_node_that_errored(self):
        answer = chat_module._answer_for(chat_module._UpstreamError(503, b''))
        assert answer.status_code == 502
        assert '503' in str(answer.detail)

    def test_a_node_that_refused_the_request(self):
        answer = chat_module._answer_for(chat_module._UpstreamError(400, b''))
        assert answer.status_code == 400
        assert 'refused the request' in str(answer.detail)

    def test_a_node_at_capacity_keeps_its_retry_after(self):
        answer = chat_module._answer_for(chat_module._UpstreamError(429, b''))
        assert answer.status_code == 429
        assert answer.headers == {'Retry-After': '30'}

    def test_a_node_that_answered_nonsense(self):
        answer = chat_module._answer_for(chat_module._UpstreamUnreadable('<html>'))
        assert 'unreadable' in str(answer.detail)

    def test_a_node_that_stopped_partway_is_not_one_that_never_answered(self):
        """A half-written reply that stops is a 504, and says so — the
        reader waited, saw text, and needs to know it is not coming
        back."""
        stalled = chat_module._answer_for(chat_module._UpstreamStale('silent'))
        absent = chat_module._answer_for(httpx.ConnectError('refused'))

        assert stalled.status_code == 504
        assert absent.status_code == 502
        assert str(stalled.detail) != str(absent.detail)


class TestRetryingWhatAnotherNodeCouldFix:
    """Which failures are worth a second node, and which are not.

    Asserted on the predicate both paths consult, so the streaming and
    non-streaming loops cannot disagree about it again — they already
    did, which is the gap this closes.
    """

    @pytest.mark.parametrize('code', [500, 502, 503, 504, 408, 429])
    def test_a_node_problem_is_worth_another_node(self, code):
        assert chat_module._worth_another_node(chat_module._UpstreamError(code, b''))

    @pytest.mark.parametrize('code', [400, 401, 403, 404, 413, 422])
    def test_a_request_problem_is_not(self, code):
        """The request does not change between nodes, so neither does
        the refusal — retrying spends two more nodes to say it twice."""
        assert not chat_module._worth_another_node(chat_module._UpstreamError(code, b''))

    def test_a_node_that_answers_nonsense_is(self):
        assert chat_module._worth_another_node(chat_module._UpstreamUnreadable('<html>502</html>'))

    def test_a_node_that_does_not_answer_is(self):
        assert chat_module._worth_another_node(httpx.ConnectError('refused'))


class TestTheNonStreamingPathRetriesABadAnswer:
    """The gap: only a transport failure moved on to another node.

    A node that was up and unwell — a vLLM still loading its weights
    answers 503 — failed the whole request on its first reply while
    healthy nodes sat idle. The streaming path had retried that since
    it was written.
    """

    async def test_a_503_moves_to_another_node_and_succeeds(self, non_stream):
        """One bad node, then a good one, and the caller never sees it."""
        pool, ctx, install = non_stream
        install([_Reply(503, 'still loading'), _Reply(200, json.dumps(_COMPLETION))])

        answer = await chat_module._non_stream_response('http://node.invalid', {}, {}, ctx)

        assert answer.id == 'done'
        assert len(pool.acquired) == 2, pool.acquired

    async def test_a_400_stops_on_the_first_node(self, non_stream):
        pool, ctx, install = non_stream
        install([_Reply(400, 'context too long')] * 3)

        with pytest.raises(HTTPException) as caught:
            await chat_module._non_stream_response('http://node.invalid', {}, {}, ctx)

        # Passed through, not flattened to 502: the caller asked for
        # something the model will not do, and a 502 would send them to
        # look at our infrastructure for it.
        assert caught.value.status_code == 400
        assert len(pool.acquired) == 1, pool.acquired

    async def test_an_unreadable_200_moves_on(self, non_stream):
        pool, ctx, install = non_stream
        install([_Reply(200, 'data: not-a-completion\n\n'), _Reply(200, json.dumps(_COMPLETION))])

        answer = await chat_module._non_stream_response('http://node.invalid', {}, {}, ctx)

        assert answer.id == 'done'
        assert len(pool.acquired) == 2, pool.acquired

    async def test_every_node_bad_reports_the_failure_not_the_search(self, non_stream):
        """The last word is what went wrong, not that we stopped looking."""
        _pool, ctx, install = non_stream
        install([_Reply(503, 'still loading')] * 3)

        with pytest.raises(HTTPException) as caught:
            await chat_module._non_stream_response('http://node.invalid', {}, {}, ctx)

        assert caught.value.status_code == 502
        assert '503' in str(caught.value.detail), caught.value.detail


class TestValidationIsLeftAlone:
    """Deliberate, and worth pinning so it is not "fixed" later.

    FastAPI's 422 names every bad field and why, per field. The OpenAI
    envelope has one `param`, so converting would throw away detail to
    gain a shape no SDK needs here — a client does not send a body it
    did not build.
    """

    async def test_a_bad_field_still_lists_fields(self, authed):
        client, headers = authed

        response = await client.post(_CHAT, json={**_body(), 'n': 100_000}, headers=headers)

        assert response.status_code == 422
        assert any(err['loc'][-1] == 'n' for err in response.json()['detail'])
