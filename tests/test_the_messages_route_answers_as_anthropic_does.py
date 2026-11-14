"""`POST /v1/messages` answers the way Anthropic's API does, whichever backend served it.

The caller signs in with igni server's own token — as a Bearer, or as an
Anthropic SDK's ``x-api-key`` — and gets Anthropic's shapes back: the
message as the provider sent it, the stream event by event, and a refusal
as Anthropic's error envelope. A stream fails over to another backend only
before its first event; after that a failure is an ``error`` event. The
pool is only ever asked for a backend speaking Anthropic's API, every slot
taken is given back, and the usage row names the backend and counts the
prompt cache.
"""

from __future__ import annotations

import json

from cryptography.fernet import Fernet
import httpx
import pytest
from sqlalchemy import text

from app.api.base_v1.schema.messages import AnthropicErrorBody
from app.config import settings
from app.services.ai.backend import BackendLease
from app.services.ai.chat.services import chat_services, ChatServices
from app.services.ai.messages.upstream import MessagesUpstream
from app.services.ai.providers.auth import AuthMethod
from app.services.ai.providers.kinds import BackendKind, Wire
from tests.chat_proxy import pools, SpyPool
from tests.cloud_fakes import FakeCloud
from tests.conftest import auth_header, create_org, create_user, make_token

_BODY = {'model': 'claude', 'max_tokens': 64, 'messages': [{'role': 'user', 'content': 'hi'}]}
_START = {'type': 'message_start', 'message': {'usage': {'input_tokens': 5, 'cache_read_input_tokens': 100}}}
_END = {'type': 'message_delta', 'usage': {'output_tokens': 7}}
_CLAUDE = BackendLease(
    lease_id='l',
    backend_id='claude',
    base_url='https://api.anthropic.com',
    model='claude',
    kind=BackendKind.ANTHROPIC,
    auth=AuthMethod.API_KEY,
    api_key='sk-ant',
)


def _sse(*events: dict) -> str:
    return ''.join(f'event: {event["type"]}\ndata: {json.dumps(event)}\n\n' for event in events)


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    monkeypatch.setattr(settings, 'secret_encryption_key', Fernet.generate_key().decode())


@pytest.fixture
async def person(db) -> tuple[str, str]:
    await create_user(db, user_id='ada', email='ada@test.com')
    org = await create_org(db, owner_user_id='ada', slug='acme', display_name='Acme')
    return make_token('ada', org_id=str(org.id)), str(org.id)


class _Provider:
    """The provider behind every lease: answers each call in turn from *answers*, recording them."""

    def __init__(self, *answers: httpx.Response) -> None:
        self.answers = list(answers)
        self.sent: list[httpx.Request] = []

    def __call__(self, **kwargs) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._answer), **kwargs)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        return self.answers[min(len(self.sent), len(self.answers)) - 1]


@pytest.fixture
def route(app):
    """The app's chat services over a pool of Claude backends and a scripted provider."""
    made: list[tuple[SpyPool, _Provider]] = []

    def build(*answers: httpx.Response) -> tuple[SpyPool, _Provider]:
        pool, provider = SpyPool(acquired=[], on=_CLAUDE), _Provider(*answers)
        upstream = MessagesUpstream(credentials=FakeCloud().credentials(), client_factory=provider)
        services = ChatServices(pools=pools(pool), messages_upstream=upstream)  # type: ignore[arg-type]
        app.dependency_overrides[chat_services] = lambda: services
        made.append((pool, provider))
        return pool, provider

    yield build
    app.dependency_overrides.pop(chat_services, None)


class TestAMessage:
    async def test_is_answered_as_sent_and_billed_with_the_cache(self, client, person, route, org_db):
        token, org = person
        message = {
            'type': 'message',
            'content': [{'type': 'text', 'text': 'hi'}],
            'usage': {'input_tokens': 5, 'cache_read_input_tokens': 100, 'output_tokens': 7},
        }
        pool, provider = route(httpx.Response(200, json=message))

        resp = await client.post('/messages', headers=auth_header(token), json=_BODY)

        assert resp.status_code == 200, resp.text
        assert resp.json() == message
        assert (pool.wires, pool.acquired, pool.released) == ([Wire.ANTHROPIC], ['lease-1'], ['lease-1'])
        assert provider.sent[0].headers['x-api-key'] == 'sk-ant'
        async with org_db(org) as session:
            row = (
                await session.execute(
                    text(
                        'SELECT model, input_tokens, output_tokens, cache_read_tokens, backend_id, kind FROM chat_usage'
                    )
                )
            ).one()
        assert tuple(row) == ('claude', 105, 7, 100, 'claude-1', 'anthropic')

    async def test_an_anthropic_sdk_signs_in_with_x_api_key(self, client, person, route):
        token, _org = person
        message = {'type': 'message', 'content': [], 'usage': {'input_tokens': 1, 'output_tokens': 1}}
        route(httpx.Response(200, json=message))

        resp = await client.post(
            '/messages', headers={'x-api-key': token, 'anthropic-version': '2023-06-01'}, json=_BODY
        )

        assert resp.status_code == 200, resp.text
        assert resp.json() == message

    async def test_no_token_is_anthropics_401(self, client, route):
        route(httpx.Response(200, json={}))

        resp = await client.post('/messages', json=_BODY)

        assert resp.status_code == 401
        assert (
            resp.json()
            == AnthropicErrorBody.of(
                message='Sign in to igni server: send its token as Authorization: Bearer, or as x-api-key',
                status_code=401,
            ).model_dump()
        )


class TestAStream:
    async def test_is_relayed_event_by_event(self, client, person, route):
        token, _org = person
        pool, _provider = route(httpx.Response(200, text=_sse(_START, _END)))

        resp = await client.post('/messages', headers=auth_header(token), json={**_BODY, 'stream': True})

        assert resp.status_code == 200
        assert resp.text == _sse(_START, _END)
        assert pool.released == ['lease-1']

    async def test_fails_over_before_its_first_event_and_gives_the_first_slot_back(self, client, person, route):
        token, _org = person
        pool, provider = route(httpx.Response(503, text='busy'), httpx.Response(200, text=_sse(_START, _END)))

        resp = await client.post('/messages', headers=auth_header(token), json={**_BODY, 'stream': True})

        assert resp.text == _sse(_START, _END)
        assert len(provider.sent) == 2
        assert (pool.acquired, pool.released, pool.wires) == (
            ['lease-1', 'lease-2'],
            ['lease-1', 'lease-2'],
            [Wire.ANTHROPIC, Wire.ANTHROPIC],
        )

    async def test_that_fails_after_its_first_event_ends_with_an_error_event_and_no_second_try(
        self, client, person, route
    ):
        token, _org = person

        class Broken(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield _sse(_START).encode()
                raise httpx.ReadError('the provider went away')

        pool, provider = route(httpx.Response(200, stream=Broken()))

        resp = await client.post('/messages', headers=auth_header(token), json={**_BODY, 'stream': True})

        assert resp.text == _sse(_START) + AnthropicErrorBody.sse_frame(
            message='No inference node answered. Try again shortly.', status_code=502
        )
        assert (len(provider.sent), pool.acquired, pool.released) == (1, ['lease-1'], ['lease-1'])
