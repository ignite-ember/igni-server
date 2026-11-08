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
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import settings
from app.services.ai.backend_provider import BackendConfigError
from app.services.ai.chat.errors import UpstreamRefusal
from app.services.ai.chat.services import chat_services, ChatServices
from app.services.ai.schemas import (
    BackendLease,
    ModelNotAvailableError,
    NoBackendAvailableError,
)
from app.services.ai.upstream import RetryPolicy, UpstreamError, UpstreamFailure, UpstreamStale, UpstreamUnreadable
from tests.chat_proxy import completions, context, drain, NO_BACKOFF, on_transport, SpyPool, streamer
from tests.conftest import auth_header, create_org, create_user, make_token

_CHAT = '/chat/completions'


def _body() -> dict:
    return {'model': 'some-model', 'messages': [{'role': 'user', 'content': 'hi'}]}


#: A minimal successful completion, for the node that answers after a
#: bad one.
_COMPLETION = {'id': 'done', 'object': 'chat.completion', 'choices': [], 'usage': {}}


def _scripted(*replies: tuple[int, str]):
    """A real client over nodes that reply a different way each time — a
    retry must reach a *new* node — repeating the last reply."""
    calls = {'n': 0}

    def node(_request: httpx.Request) -> httpx.Response:
        status_code, text = replies[min(calls['n'], len(replies) - 1)]
        calls['n'] += 1
        return httpx.Response(status_code, text=text)

    return on_transport(node)


def _dead_node(_request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError('connection refused')


@pytest.fixture
async def authed(client, db):
    await create_user(db, user_id='envelope-user', email='envelope@test.com')
    org = await create_org(db, owner_user_id='envelope-user', slug='envelope-org', display_name='E')
    return client, auth_header(make_token('envelope-user', org_id=str(org.id)))


@pytest.fixture
def chat_proxy(app):
    """Serve the chat routes from a fresh `ChatServices` over *upstream*."""

    def install(upstream=None) -> None:
        services = ChatServices(upstream=upstream, retry=NO_BACKOFF)
        app.dependency_overrides[chat_services] = lambda: services

    yield install
    app.dependency_overrides.pop(chat_services, None)


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

    async def test_a_dead_node_says_so_where_an_sdk_looks(self, authed, chat_proxy):
        client, headers = authed
        chat_proxy(on_transport(_dead_node))
        lease = BackendLease(
            lease_id='l1',
            backend_id='gpu-01',
            api_key=None,
            base_url='http://gone.invalid/v1',
            model='m',
        )
        pool = AsyncMock()
        pool.acquire = AsyncMock(return_value=lease)

        with _with_pool(pool):
            response = await client.post(_CHAT, json=_body(), headers=headers)

        assert response.status_code == 502, response.text
        message, kind = _read(response)
        # The whole point: not "Unknown model error".
        assert message == 'No inference node answered. Try again shortly.', message
        assert kind == 'server_error'

    async def test_the_old_shape_is_still_there(self, authed):
        """`detail` is kept, so nothing that reads it had to change."""
        client, headers = authed
        pool = _pool(ModelNotAvailableError(model='nope', available=['m']))

        with _with_pool(pool):
            response = await client.post(_CHAT, json=_body(), headers=headers)

        assert response.status_code == 404
        assert response.json()['detail'] == response.json()['error']['message']
        assert response.json()['detail'] == str(ModelNotAvailableError(model='nope', available=['m']))


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

    Different transport, same API. These drive the real stream and the
    real upstream client, over a node that answers every attempt with
    one status.
    """

    async def _frames(self, status_code: int) -> list[str]:
        upstream = on_transport(lambda _request: httpx.Response(status_code, content=b'upstream said no'))
        return await drain(streamer(upstream).open(context(SpyPool())).frames())

    def _error_frame(self, frames: list[str]) -> dict:
        for frame in frames:
            if frame.startswith('data: ') and '"error"' in frame:
                return json.loads(frame[6:])
        raise AssertionError(f'no error frame in {frames}')

    async def test_the_frame_is_the_envelope_an_sdk_reads(self):
        frame = self._error_frame(await self._frames(503))

        assert frame == {
            'error': {
                'message': 'The inference node returned 503.',
                'type': 'server_error',
                'code': None,
                'param': None,
            }
        }

    async def test_a_refused_request_is_not_a_server_error(self):
        """A 400 is about the request, and is typed as such."""
        error = self._error_frame(await self._frames(400))['error']

        assert error['type'] == 'invalid_request_error', error
        assert error['message'] == 'The inference node refused the request (400).'

    async def test_a_4xx_is_not_tried_on_three_nodes(self):
        """The retry comments are the visible cost of asking again."""
        frames = await self._frames(400)

        assert ': retry\n\n' not in frames, frames

    async def test_a_node_that_refuses_the_connection_fails_over_and_then_says_so(self):
        """Every transport failure is an `UpstreamFailure` now, so a refused
        connection moves to another node — it used to escape the stream,
        which caught only `ReadTimeout` — and ends in the same 502 frame
        the non-streaming path answers with."""
        pool = SpyPool()
        nodes: list[str] = []

        def refusing(request: httpx.Request) -> httpx.Response:
            nodes.append(request.url.host)
            raise httpx.ConnectError('connection refused')

        frames = await drain(streamer(on_transport(refusing)).open(context(pool)).frames())

        assert frames.count(': retry\n\n') == 2, frames
        assert len(nodes) == 3
        assert pool.acquired == ['lease-1', 'lease-2', 'lease-3']
        assert self._error_frame(frames)['error']['message'] == 'No inference node answered. Try again shortly.'
        assert sorted(pool.released) == sorted(pool.acquired)


class TestTheSentenceNamesTheFailure:
    """Each failure gets its own status and sentence, asserted on the
    refusal both paths raise rather than through either one."""

    def test_a_node_that_errored(self):
        answer = UpstreamRefusal(UpstreamError(503, b''))
        assert (answer.status_code, answer.detail, answer.retry_after) == (
            502,
            'The inference node returned 503.',
            None,
        )

    def test_a_node_that_refused_the_request(self):
        answer = UpstreamRefusal(UpstreamError(400, b''))
        assert (answer.status_code, answer.detail) == (400, 'The inference node refused the request (400).')

    def test_a_node_at_capacity_keeps_its_retry_after(self):
        answer = UpstreamRefusal(UpstreamError(429, b''))
        assert answer.status_code == 429
        assert answer.retry_after == 30

    def test_a_node_that_answered_nonsense(self):
        answer = UpstreamRefusal(UpstreamUnreadable('<html>'))
        assert (answer.status_code, answer.detail) == (502, 'The inference node returned an unreadable response.')

    def test_a_node_that_stopped_partway_is_not_one_that_never_answered(self):
        """A half-written reply that stops is a 504, and says so — the
        reader waited, saw text, and needs to know it is not coming
        back."""
        stalled = UpstreamRefusal(UpstreamStale('silent'))
        absent = UpstreamRefusal(UpstreamFailure.from_transport(httpx.ConnectError('refused')))

        assert stalled.status_code == 504
        assert absent.status_code == 502
        assert stalled.detail != absent.detail

    @pytest.mark.parametrize(
        ('cause', 'label', 'status_code'),
        [
            (httpx.ReadTimeout('t'), 'read_timeout', 504),
            (httpx.ConnectTimeout('t'), 'connect_timeout', 504),
            (httpx.PoolTimeout('t'), 'pool_timeout', 504),
            (httpx.ConnectError('c'), 'connect_error', 502),
            (httpx.RemoteProtocolError('p'), 'remote_protocol_error', 502),
        ],
    )
    def test_a_transport_failure_is_named_for_its_cause(self, cause, label, status_code):
        failure = UpstreamFailure.from_transport(cause)

        assert (failure.label, failure.answer_status, failure.worth_another_node) == (label, status_code, True)

    def test_the_log_labels_are_unchanged(self):
        assert UpstreamStale('x').label == 'stale'
        assert UpstreamError(503, b'').label == '503'


class TestRetryingWhatAnotherNodeCouldFix:
    """Which failures are worth a second node, and which are not —
    decided by the failure itself, which both paths consult."""

    @pytest.mark.parametrize('code', [500, 502, 503, 504, 408, 429])
    def test_a_node_problem_is_worth_another_node(self, code):
        assert UpstreamError(code, b'').worth_another_node

    @pytest.mark.parametrize('code', [400, 401, 403, 404, 413, 422])
    def test_a_request_problem_is_not(self, code):
        """The request does not change between nodes, so neither does
        the refusal — retrying spends two more nodes to say it twice."""
        assert not UpstreamError(code, b'').worth_another_node

    def test_a_node_that_answers_nonsense_is(self):
        assert UpstreamUnreadable('<html>502</html>').worth_another_node

    def test_a_node_that_does_not_answer_is(self):
        assert UpstreamFailure.from_transport(httpx.ConnectError('refused')).worth_another_node


class TestTheNonStreamingPathRetriesABadAnswer:
    """A node that is up and unwell — a vLLM still loading its weights
    answers 503 — is retried on another, as the streaming path does."""

    async def test_a_503_moves_to_another_node_and_succeeds(self):
        """One bad node, then a good one, and the caller never sees it."""
        pool = SpyPool()
        upstream = _scripted((503, 'still loading'), (200, json.dumps(_COMPLETION)))

        answer = await completions(upstream).complete(context(pool))

        assert answer.id == 'done'
        assert len(pool.acquired) == 2, pool.acquired

    async def test_a_400_stops_on_the_first_node(self):
        pool = SpyPool()

        with pytest.raises(UpstreamRefusal) as caught:
            await completions(_scripted((400, 'context too long'))).complete(context(pool))

        # Passed through, not flattened to 502: the caller asked for
        # something the model will not do.
        assert caught.value.status_code == 400
        assert len(pool.acquired) == 1, pool.acquired

    async def test_an_unreadable_200_moves_on(self):
        pool = SpyPool()
        upstream = _scripted((200, 'data: not-a-completion\n\n'), (200, json.dumps(_COMPLETION)))

        answer = await completions(upstream).complete(context(pool))

        assert answer.id == 'done'
        assert len(pool.acquired) == 2, pool.acquired

    async def test_a_200_whose_choice_has_no_message_moves_on_too(self):
        """JSON, but not a completion: a choice with no `message`. It
        used to escape as a `KeyError` and a bare 500 blaming this server;
        it is an unreadable answer like any other, and another node is
        asked."""
        pool = SpyPool()
        broken = json.dumps({'id': 'half', 'choices': [{'index': 0, 'finish_reason': 'stop'}]})
        upstream = _scripted((200, broken), (200, json.dumps(_COMPLETION)))

        answer = await completions(upstream).complete(context(pool))

        assert answer.id == 'done'
        assert len(pool.acquired) == 2, pool.acquired

    async def test_a_200_with_no_completion_anywhere_is_a_502_that_says_so(self):
        broken = json.dumps({'id': 'half', 'choices': [{'index': 0}]})

        with pytest.raises(UpstreamRefusal) as caught:
            await completions(_scripted((200, broken))).complete(context(SpyPool()))

        assert caught.value.status_code == 502
        assert caught.value.detail == 'The inference node returned an unreadable response.'

    async def test_every_node_bad_reports_the_failure_not_the_search(self):
        """The last word is what went wrong, not that we stopped looking."""
        with pytest.raises(UpstreamRefusal) as caught:
            await completions(_scripted((503, 'still loading'))).complete(context(SpyPool()))

        assert caught.value.status_code == 502
        assert caught.value.detail == 'The inference node returned 503.'


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


class TestHowManyNodesAndHowLong:
    """The retry policy both paths consult: three attempts, a longer pause before each."""

    def test_it_keeps_going_until_the_last_attempt(self):
        policy = RetryPolicy()
        failure = UpstreamError(503, b'')

        assert [policy.gives_up(attempt=n, failure=failure) for n in (1, 2, 3)] == [False, False, True]

    def test_it_stops_at_once_on_what_no_node_would_fix(self):
        assert RetryPolicy().gives_up(attempt=1, failure=UpstreamError(400, b''))

    def test_a_redirect_is_a_502_and_not_asked_again(self):
        """Below 400 and not 200: neither the caller's fault nor one another node fixes."""
        failure = UpstreamError(302, b'')

        assert (failure.answer_status, failure.sentence, failure.worth_another_node) == (
            502,
            'The inference node returned 302.',
            False,
        )

    async def test_each_pause_is_longer_than_the_last(self, monkeypatch):
        pauses: list[float] = []

        async def no_wait(seconds: float) -> None:
            pauses.append(seconds)

        monkeypatch.setattr('app.services.ai.upstream.asyncio.sleep', no_wait)

        for attempt in (1, 2, 3):
            await RetryPolicy(backoff=0.25).pause(attempt)

        assert pauses == [0.25, 0.5, 0.75]
