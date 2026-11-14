"""`POST /v1/messages`: Claude, on Anthropic's own API, wherever it is served.

Four kinds serve it — Anthropic's API (or a compatible gateway), Bedrock,
Vertex AI and Microsoft Foundry — and each takes the same Messages body
with its own address, version and signature. These tests pin exactly
what each is sent, that Bedrock's binary event stream comes back as the
SSE events every other kind sends, what is billed (the prompt cache
included), and what the caller sees: Anthropic's error shape, a failover
only before the first event, and a model on the wrong API sent to the
right endpoint. Nothing here reaches a provider: the transports and the
credentials are fakes, and the egress guard would refuse anything else.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
import struct
from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet
import httpx
from pydantic import ValidationError
import pytest
from sqlalchemy import text

from app.api.base_v1.schema.messages import AnthropicErrorBody
from app.config import settings
from app.services.ai.chat.services import chat_services, ChatServices
from app.services.ai.messages.calls import (
    ANTHROPIC_VERSION,
    BEDROCK_VERSION,
    CallerHeaders,
    prepare,
    sigv4,
    VERTEX_VERSION,
)
from app.services.ai.messages.upstream import _bedrock_message, MessagesUpstream
from app.services.ai.messages.usage import MessagesUsage, StreamTally
from app.services.ai.providers.auth import AuthMethod, METHODS
from app.services.ai.providers.credentials import AccessToken, AwsKeys, CloudCredentials
from app.services.ai.providers.kinds import BackendKind, Wire
from app.services.ai.schemas import BackendLease, InferenceBackend, ModelNotAvailableError
from app.services.ai.upstream import UpstreamError
from tests.conftest import auth_header, create_org, create_user, make_token

_BODY = {'model': 'claude', 'max_tokens': 64, 'messages': [{'role': 'user', 'content': 'hi'}]}
_KEYS = AwsKeys(access_key_id='AKIDEXAMPLE', secret_access_key='SECRET-MATERIAL', session_token='SESSION')


def _backend(**fields) -> InferenceBackend:
    return InferenceBackend.model_validate({'id': 'b', 'models': ['claude'], **fields})


def _lease(kind: BackendKind, auth: AuthMethod, **fields) -> BackendLease:
    backend = {
        BackendKind.ANTHROPIC: 'https://api.anthropic.com',
        BackendKind.AZURE_ANTHROPIC: 'https://res.services.ai.azure.com/anthropic',
        BackendKind.VERTEX_ANTHROPIC: 'https://europe-west4-aiplatform.googleapis.com/v1/projects/p/locations/'
        'europe-west4/publishers/anthropic/models',
        BackendKind.BEDROCK: 'https://bedrock-runtime.eu-central-1.amazonaws.com',
    }[kind]
    return BackendLease(lease_id='l', backend_id='b', base_url=backend, model='claude', kind=kind, auth=auth, **fields)


class _Credentials(CloudCredentials):
    """Hands out tok-1, tok-2, ... and fixed AWS keys, counting each fetch."""

    def __init__(self) -> None:
        self.fetched = 0

        def bearer(settings, secret):
            async def fetch():
                self.fetched += 1
                return AccessToken(value=f'tok-{self.fetched}', expires_at=float('inf'))

            return fetch

        def aws(settings, secret):
            async def fetch():
                self.fetched += 1
                return AccessToken(value=_KEYS, expires_at=float('inf'))

            return fetch

        super().__init__(
            fetches={
                **{(kind, m): bearer for kind in BackendKind for m in AuthMethod if kind is not BackendKind.BEDROCK},
                **{(BackendKind.BEDROCK, m): aws for m in AuthMethod},
            }
        )


class TestClaudesBackends:
    def test_each_claude_kind_speaks_the_anthropic_api(self):
        claude = [kind for kind in BackendKind if kind.wire is Wire.ANTHROPIC]
        assert claude == [
            BackendKind.ANTHROPIC,
            BackendKind.BEDROCK,
            BackendKind.VERTEX_ANTHROPIC,
            BackendKind.AZURE_ANTHROPIC,
        ]

    def test_bedrock_offers_the_servers_role_an_assumed_role_and_access_keys(self):
        bedrock = METHODS[BackendKind.BEDROCK]
        assert list(bedrock) == [AuthMethod.AMBIENT, AuthMethod.ASSUME_ROLE, AuthMethod.ACCESS_KEYS]
        assert (bedrock[AuthMethod.ASSUME_ROLE].settings, bedrock[AuthMethod.ASSUME_ROLE].optional) == (
            ('role_arn',),
            ('external_id',),
        )
        assert bedrock[AuthMethod.ASSUME_ROLE].secret is None
        assert bedrock[AuthMethod.ACCESS_KEYS].secret == 'credential'

    def test_claude_on_vertex_and_foundry_signs_in_as_their_openai_models_do(self):
        assert METHODS[BackendKind.VERTEX_ANTHROPIC] == METHODS[BackendKind.VERTEX_OPENAI]
        assert METHODS[BackendKind.AZURE_ANTHROPIC] == METHODS[BackendKind.AZURE_OPENAI]

    @pytest.mark.parametrize(
        ('fields', 'base_url'),
        [
            ({'kind': 'bedrock', 'region': 'eu-central-1'}, 'https://bedrock-runtime.eu-central-1.amazonaws.com'),
            (
                {'kind': 'vertex_anthropic', 'project': 'p', 'region': 'us-east5'},
                'https://us-east5-aiplatform.googleapis.com/v1/projects/p/locations/us-east5/publishers/anthropic/models',
            ),
            ({'kind': 'anthropic', 'api_key': 'k'}, 'https://api.anthropic.com'),
            ({'kind': 'anthropic', 'api_key': 'k', 'base_url': 'https://gw.internal'}, 'https://gw.internal'),
        ],
    )
    def test_an_address_its_kind_fixes_is_built(self, fields, base_url):
        assert _backend(**fields).base_url == base_url

    @pytest.mark.parametrize(
        ('fields', 'says'),
        [
            ({'kind': 'bedrock'}, 'needs its region'),
            ({'kind': 'anthropic', 'api_key': 'k', 'region': 'r'}, 'Vertex or Bedrock backend only'),
            ({'kind': 'bedrock', 'region': 'r', 'project': 'p'}, 'Vertex backend only'),
            ({'kind': 'bedrock', 'region': 'r', 'auth': 'assume_role'}, 'needs role_arn'),
            (
                {'kind': 'bedrock', 'region': 'r', 'auth': 'access_keys', 'auth_settings': {'access_key_id': 'A'}},
                'credential',
            ),
            ({'kind': 'anthropic'}, 'needs its key'),
        ],
    )
    def test_a_claude_backend_missing_what_it_needs_is_refused(self, fields, says):
        with pytest.raises(ValidationError, match=says):
            _backend(**fields)

    def test_an_assumed_roles_external_id_is_optional(self):
        role = {'kind': 'bedrock', 'region': 'r', 'auth': 'assume_role'}
        assert _backend(**role, auth_settings={'role_arn': 'arn:aws:iam::1:role/r'}).auth is AuthMethod.ASSUME_ROLE
        with_id = _backend(**role, auth_settings={'role_arn': 'arn:aws:iam::1:role/r', 'external_id': 'x'})
        assert with_id.auth_settings['external_id'] == 'x'


class TestEachKindIsSentWhatItTakes:
    async def test_anthropics_api_gets_the_body_its_key_and_the_callers_version_and_betas(self):
        lease = _lease(BackendKind.ANTHROPIC, AuthMethod.API_KEY, api_key='sk-ant', model_ids={'claude': 'claude-x'})
        asked = CallerHeaders(version='2024-01-01', betas=('a', 'b'))

        call = await prepare(lease, _BODY, stream=False, asked=asked, credentials=_Credentials())

        assert call.url == 'https://api.anthropic.com/v1/messages'
        assert call.headers == {
            'content-type': 'application/json',
            'anthropic-beta': 'a,b',
            'anthropic-version': '2024-01-01',
            'x-api-key': 'sk-ant',
        }
        assert json.loads(call.content) == {**_BODY, 'model': 'claude-x'}

    async def test_a_caller_naming_no_version_gets_the_default(self):
        lease = _lease(BackendKind.ANTHROPIC, AuthMethod.API_KEY, api_key='k')
        call = await prepare(lease, _BODY, stream=False, asked=CallerHeaders(), credentials=_Credentials())
        assert call.headers['anthropic-version'] == ANTHROPIC_VERSION
        assert 'anthropic-beta' not in call.headers

    async def test_foundry_with_a_managed_identity_is_sent_the_token(self):
        lease = _lease(BackendKind.AZURE_ANTHROPIC, AuthMethod.MANAGED_IDENTITY, auth_settings={'client_id': 'c'})
        call = await prepare(lease, _BODY, stream=False, asked=CallerHeaders(), credentials=_Credentials())
        assert call.url == 'https://res.services.ai.azure.com/anthropic/v1/messages'
        assert call.headers['authorization'] == 'Bearer tok-1'
        assert 'x-api-key' not in call.headers

    @pytest.mark.parametrize(('stream', 'action'), [(False, 'rawPredict'), (True, 'streamRawPredict')])
    async def test_vertex_names_the_model_in_the_address_and_the_version_in_the_body(self, stream, action):
        lease = _lease(BackendKind.VERTEX_ANTHROPIC, AuthMethod.AMBIENT, model_ids={'claude': 'claude-x@2026'})
        body = {**_BODY, 'stream': stream}

        call = await prepare(lease, body, stream=stream, asked=CallerHeaders(betas=('b',)), credentials=_Credentials())

        assert call.url.endswith(f'/publishers/anthropic/models/claude-x@2026:{action}')
        assert call.headers['authorization'] == 'Bearer tok-1'
        assert call.headers['anthropic-beta'] == 'b'
        sent = json.loads(call.content)
        assert 'model' not in sent
        assert sent['anthropic_version'] == VERTEX_VERSION
        assert sent['stream'] is stream

    @pytest.mark.parametrize(
        ('stream', 'action', 'accept'),
        [
            (False, 'invoke', 'application/json'),
            (True, 'invoke-with-response-stream', 'application/vnd.amazon.eventstream'),
        ],
    )
    async def test_bedrock_names_the_model_in_the_address_and_takes_betas_in_the_body(self, stream, action, accept):
        lease = _lease(
            BackendKind.BEDROCK,
            AuthMethod.ASSUME_ROLE,
            region='eu-central-1',
            auth_settings={'role_arn': 'arn:aws:iam::1:role/r'},
            model_ids={'claude': 'eu.anthropic.claude-x-v1:0'},
        )
        body = {**_BODY, 'stream': stream}

        call = await prepare(lease, body, stream=stream, asked=CallerHeaders(betas=('b',)), credentials=_Credentials())

        assert (
            call.url
            == f'https://bedrock-runtime.eu-central-1.amazonaws.com/model/eu.anthropic.claude-x-v1%3A0/{action}'
        )
        assert call.headers['accept'] == accept
        sent = json.loads(call.content)
        assert 'model' not in sent and 'stream' not in sent
        assert (sent['anthropic_version'], sent['anthropic_beta']) == (BEDROCK_VERSION, ['b'])
        assert 'anthropic-beta' not in call.headers

    async def test_bedrock_is_signed_for_its_region_over_exactly_the_body_sent(self, monkeypatch):
        """The signature, recomputed by botocore itself from the same inputs at the same instant."""
        fixed = dt.datetime(2026, 11, 14, 12, 0, 0)

        class Clock(dt.datetime):
            @classmethod
            def utcnow(cls):
                return fixed

            @classmethod
            def now(cls, tz=None):
                return fixed.replace(tzinfo=tz) if tz else fixed

        monkeypatch.setattr('botocore.auth.datetime.datetime', Clock)
        lease = _lease(BackendKind.BEDROCK, AuthMethod.AMBIENT, region='eu-central-1')

        call = await prepare(lease, _BODY, stream=False, asked=CallerHeaders(), credentials=_Credentials())

        expected = sigv4(
            _KEYS,
            'eu-central-1',
            call.url,
            {'content-type': 'application/json', 'accept': 'application/json'},
            call.content,
        )
        assert call.headers['Authorization'] == expected['Authorization']
        assert call.headers['Authorization'].startswith(
            'AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20261114/eu-central-1/bedrock/aws4_request'
        )
        assert call.headers['X-Amz-Security-Token'] == 'SESSION'
        other = sigv4(_KEYS, 'eu-central-1', call.url, {'content-type': 'application/json'}, call.content + b' ')
        assert other['Authorization'] != call.headers['Authorization']
        assert 'SECRET-MATERIAL' not in json.dumps(call.headers)


def _event_stream_message(headers: dict[str, str], payload: bytes) -> bytes:
    """One AWS event-stream message, as Bedrock frames it: string headers, both CRCs."""
    encoded = b''
    for name, value in headers.items():
        raw = value.encode()
        encoded += struct.pack('>B', len(name)) + name.encode() + struct.pack('>BH', 7, len(raw)) + raw
    total = 12 + len(encoded) + len(payload) + 4
    prelude = struct.pack('>II', total, len(encoded))
    prelude += struct.pack('>I', binascii.crc32(prelude) & 0xFFFFFFFF)
    body = prelude + encoded + payload
    return body + struct.pack('>I', binascii.crc32(body) & 0xFFFFFFFF)


def _chunk(event: dict) -> bytes:
    inner = base64.b64encode(json.dumps(event).encode()).decode()
    return _event_stream_message(
        {':message-type': 'event', ':event-type': 'chunk', ':content-type': 'application/json'},
        json.dumps({'bytes': inner}).encode(),
    )


_START = {'type': 'message_start', 'message': {'usage': {'input_tokens': 5, 'cache_read_input_tokens': 100}}}
_DELTA = {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'Hello'}}
_END = {'type': 'message_delta', 'usage': {'output_tokens': 7}}


class _Provider:
    """A provider over a mock transport: refuses the first *refusals* calls, then answers with *answer*."""

    def __init__(self, answer: httpx.Response, *, refusals: int = 0, status: int = 401) -> None:
        self.answer = answer
        self.refusals = refusals
        self.status = status
        self.sent: list[httpx.Request] = []

    def __call__(self, **kwargs) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._answer), **kwargs)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        if len(self.sent) <= self.refusals:
            return httpx.Response(self.status, text='refused')
        return self.answer


def _sse(*events: dict) -> str:
    return ''.join(f'event: {event["type"]}\ndata: {json.dumps(event)}\n\n' for event in events)


class TestBedrocksStreamComesBackAsAnthropicsEvents:
    async def test_each_chunk_is_the_event_it_carries(self):
        stream = _chunk(_START) + _chunk(_DELTA) + _chunk(_END)
        provider = _Provider(httpx.Response(200, content=stream))
        upstream = MessagesUpstream(client_factory=provider, credentials=_Credentials())
        lease = _lease(BackendKind.BEDROCK, AuthMethod.AMBIENT, region='eu-central-1')

        events = [e async for e in upstream.stream(lease=lease, body=_BODY, asked=CallerHeaders())]

        assert [e.event for e in events] == [_START, _DELTA, _END]
        assert events[0].frame == f'event: message_start\ndata: {json.dumps(_START)}\n\n'

    @pytest.mark.parametrize(
        ('exception', 'status'), [('throttlingException', 429), ('validationException', 400), ('somethingNew', 502)]
    )
    def test_an_exception_event_is_the_http_failure_it_stands_for(self, exception, status):
        with pytest.raises(UpstreamError) as raised:
            _bedrock_message({':message-type': 'exception', ':exception-type': exception}, b'{"message": "x"}')
        assert raised.value.status_code == status

    def test_an_event_that_is_not_a_chunk_says_nothing(self):
        assert _bedrock_message({':message-type': 'event', ':event-type': 'initial-response'}, b'{}') == []


class TestTheProvidersCountIsWhatIsBilled:
    def test_the_prompt_is_its_three_parts_together(self):
        usage = MessagesUsage()
        usage.take({'input_tokens': 5, 'cache_read_input_tokens': 100, 'cache_creation_input_tokens': 20})
        usage.take({'output_tokens': 7})
        assert (usage.input_tokens, usage.cache_read, usage.cache_write, usage.output, usage.total_tokens) == (
            125,
            100,
            20,
            7,
            132,
        )

    def test_a_stream_reads_the_prompt_from_its_start_and_the_output_from_its_last_delta(self):
        tally = StreamTally()
        for event in (_START, _DELTA, {'type': 'message_delta', 'usage': {'output_tokens': 3}}, _END):
            tally.absorb(event)
        assert (tally.usage.input_tokens, tally.usage.output, tally.text) == (105, 7, ['Hello'])

    def test_a_failed_attempt_is_not_billed(self):
        tally = StreamTally()
        tally.absorb(_START)
        tally.start_over()
        assert (tally.usage.reported, tally.text) == (False, [])


class TestARefusedCredentialIsRenewedOnce:
    async def test_a_token_kind_is_retried_once_with_a_fresh_token(self):
        message = {'type': 'message', 'content': [], 'usage': {'input_tokens': 1, 'output_tokens': 1}}
        provider = _Provider(httpx.Response(200, json=message), refusals=1)
        credentials = _Credentials()
        upstream = MessagesUpstream(client_factory=provider, credentials=credentials)
        lease = _lease(BackendKind.VERTEX_ANTHROPIC, AuthMethod.AMBIENT)

        assert await upstream.complete(lease=lease, body=_BODY, asked=CallerHeaders()) == message
        assert [r.headers['authorization'] for r in provider.sent] == ['Bearer tok-1', 'Bearer tok-2']

    async def test_a_key_refused_is_not_asked_again(self):
        provider = _Provider(httpx.Response(200, json={}), refusals=1)
        upstream = MessagesUpstream(client_factory=provider, credentials=_Credentials())
        with pytest.raises(UpstreamError):
            await upstream.complete(
                lease=_lease(BackendKind.ANTHROPIC, AuthMethod.API_KEY, api_key='k'), body=_BODY, asked=CallerHeaders()
            )
        assert len(provider.sent) == 1


class TestAModelIsServedOnItsOwnApi:
    def test_claude_asked_for_on_chat_completions_is_sent_to_messages(self):
        error = ModelNotAvailableError(model='claude', available=['gpt'], served_on=Wire.ANTHROPIC)
        assert '/v1/messages' in str(error)

    async def test_the_pool_never_hands_a_backend_of_the_other_api(self, tmp_path):
        from app.services.ai.backend_pool import InferenceBackendPool
        from app.services.ai.backend_provider import InferenceBackendProvider
        from tests.test_inference_backend_pool import FakeRedis, FakeRegistry

        backends = [
            InferenceBackend(id='gpu', base_url='http://gpu/v1', models=['shared', 'vllm-only']),
            _backend(id='claude', kind='anthropic', api_key='k', models=['claude', 'shared']),
        ]
        path = tmp_path / 'b.json'
        path.write_text(json.dumps([b.model_dump(mode='json') for b in backends]))
        pool = InferenceBackendPool(provider=InferenceBackendProvider(path=str(path), registry=FakeRegistry()))
        redis = FakeRedis()

        async def _redis():
            return redis

        pool._get_redis = _redis  # type: ignore[method-assign]

        assert (await pool.acquire('u', model='shared')).backend_id == 'gpu'
        assert (await pool.acquire('u', model='shared', wire=Wire.ANTHROPIC)).backend_id == 'claude'
        assert (await pool.acquire('u')).backend_id == 'gpu'
        with pytest.raises(ModelNotAvailableError, match='/v1/messages'):
            await pool.acquire('u', model='claude')
        with pytest.raises(ModelNotAvailableError, match='/v1/chat/completions'):
            await pool.acquire('u', model='vllm-only', wire=Wire.ANTHROPIC)


# ── The route, end to end ─────────────────────────────────────────────


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    monkeypatch.setattr(settings, 'secret_encryption_key', Fernet.generate_key().decode())


@pytest.fixture
async def person(db):
    await create_user(db, user_id='ada', email='ada@test.com')
    org = await create_org(db, owner_user_id='ada', slug='acme', display_name='Acme')
    return make_token('ada', org_id=str(org.id)), str(org.id)


class _Route:
    """The app with a fake pool of Anthropic backends and a scripted provider."""

    def __init__(self, app, provider: _Provider, *, leases: list[BackendLease]) -> None:
        self.pool = AsyncMock()
        self.pool.acquire = AsyncMock(side_effect=list(leases))
        self.provider = provider
        services = ChatServices(messages_upstream=MessagesUpstream(client_factory=provider, credentials=_Credentials()))
        app.dependency_overrides[chat_services] = lambda: services
        self.patch = patch('app.services.ai.pool_manager.PoolManager.get_pool', new=lambda _self: self.pool)

    async def post(self, client, headers: dict[str, str], body: dict | None = None) -> httpx.Response:
        with self.patch:
            return await client.post('/messages', headers=headers, json=body or _BODY)


def _anthropic_lease(n: int = 1) -> BackendLease:
    return _lease(BackendKind.ANTHROPIC, AuthMethod.API_KEY, api_key='k').model_copy(
        update={'lease_id': f'l{n}', 'backend_id': f'claude-{n}'}
    )


@pytest.fixture
def cleanup(app):
    yield
    app.dependency_overrides.pop(chat_services, None)


class TestTheRoute:
    async def test_a_message_is_answered_and_its_usage_billed_with_the_cache(
        self, client, app, person, org_db, cleanup
    ):
        token, org = person
        message = {
            'type': 'message',
            'content': [{'type': 'text', 'text': 'hi'}],
            'usage': {'input_tokens': 5, 'cache_read_input_tokens': 100, 'output_tokens': 7},
        }
        route = _Route(app, _Provider(httpx.Response(200, json=message)), leases=[_anthropic_lease()])

        resp = await route.post(client, auth_header(token))

        assert resp.status_code == 200, resp.text
        assert resp.json() == message
        assert route.pool.acquire.await_args.kwargs['wire'] is Wire.ANTHROPIC
        async with org_db(org) as session:
            row = (
                await session.execute(
                    text(
                        'SELECT model, input_tokens, output_tokens, cache_read_tokens, backend_id, kind FROM chat_usage'
                    )
                )
            ).one()
        assert tuple(row) == ('claude', 105, 7, 100, 'claude-1', 'anthropic')

    async def test_an_anthropic_sdk_signs_in_with_x_api_key(self, client, app, person, cleanup):
        token, _org = person
        message = {'type': 'message', 'content': [], 'usage': {'input_tokens': 1, 'output_tokens': 1}}
        route = _Route(app, _Provider(httpx.Response(200, json=message)), leases=[_anthropic_lease()])

        resp = await route.post(client, {'x-api-key': token, 'anthropic-version': '2023-06-01'})

        assert resp.status_code == 200, resp.text

    async def test_no_token_is_an_anthropic_shaped_401(self, client, app, cleanup):
        route = _Route(app, _Provider(httpx.Response(200, json={})), leases=[])

        resp = await route.post(client, {})

        assert resp.status_code == 401
        body = resp.json()
        assert body['type'] == 'error' and body['error']['type'] == 'authentication_error'

    async def test_a_stream_is_relayed_event_by_event(self, client, app, person, cleanup):
        token, _org = person
        provider = _Provider(httpx.Response(200, text=_sse(_START, _DELTA, _END)))
        route = _Route(app, provider, leases=[_anthropic_lease()])

        resp = await route.post(client, auth_header(token), {**_BODY, 'stream': True})

        assert resp.status_code == 200
        assert resp.text == _sse(_START, _DELTA, _END)

    async def test_a_stream_fails_over_before_its_first_event(self, client, app, person, cleanup):
        token, _org = person
        provider = _Provider(httpx.Response(200, text=_sse(_START, _END)), refusals=1, status=503)
        route = _Route(app, provider, leases=[_anthropic_lease(1), _anthropic_lease(2)])

        resp = await route.post(client, auth_header(token), {**_BODY, 'stream': True})

        assert resp.text == _sse(_START, _END)
        assert route.pool.acquire.await_count == 2

    async def test_a_stream_that_fails_after_its_first_event_ends_with_an_error_event(
        self, client, app, person, cleanup
    ):
        token, _org = person

        class Broken(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield _sse(_START).encode()
                raise httpx.ReadError('the provider went away')

        route = _Route(app, _Provider(httpx.Response(200, stream=Broken())), leases=[_anthropic_lease(1)])

        resp = await route.post(client, auth_header(token), {**_BODY, 'stream': True})

        assert resp.text.startswith(_sse(_START))
        assert resp.text.count('event: message_start') == 1
        assert resp.text.endswith(
            AnthropicErrorBody.sse_frame(message='No inference node answered. Try again shortly.', status_code=502)
        )
        assert route.pool.acquire.await_count == 1
