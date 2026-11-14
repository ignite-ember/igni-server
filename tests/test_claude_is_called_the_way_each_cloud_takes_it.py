"""Claude is called the way each cloud takes it, and its answer is read the same way from all of them.

Four kinds serve Claude — Anthropic's API (or a compatible gateway),
Bedrock, Vertex AI and Microsoft Foundry — each taking the same Messages
body with its own address, version and signature. These tests pin exactly
what each is sent; check Bedrock's SigV4 signature against botocore's own
signer, independently of the code under test; decode Bedrock's binary
event stream back into Anthropic's events; and hold the usage read from
either kind of answer. Nothing reaches a provider.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
import struct

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
import httpx
import pytest

from app.api.base_v1.schema.messages import MessagesRequest
from app.services.ai.backend import BackendLease
from app.services.ai.messages.calls import (
    ANTHROPIC_VERSION,
    BEDROCK_VERSION,
    CallerHeaders,
    MessagesCall,
    MessagesCalls,
    VERTEX_VERSION,
)
from app.services.ai.messages.events import AnthropicUsage, event_of
from app.services.ai.messages.upstream import MessagesUpstream
from app.services.ai.messages.usage import MessagesUsage, StreamTally
from app.services.ai.providers.auth import AuthMethod
from app.services.ai.providers.credentials import CloudCredentials
from app.services.ai.providers.kinds import BackendKind
from app.services.ai.upstream import UpstreamError
from tests.cloud_fakes import FakeCloud, ScriptedProvider

_BODY = {'model': 'claude', 'max_tokens': 64, 'messages': [{'role': 'user', 'content': 'hi'}]}
_REQUEST = MessagesRequest(**_BODY)
_ADDRESS = {
    BackendKind.ANTHROPIC: 'https://api.anthropic.com',
    BackendKind.AZURE_ANTHROPIC: 'https://res.services.ai.azure.com/anthropic',
    BackendKind.VERTEX_ANTHROPIC: (
        'https://europe-west4-aiplatform.googleapis.com/v1/projects/p/locations/europe-west4/publishers/anthropic/models'
    ),
    BackendKind.BEDROCK: 'https://bedrock-runtime.eu-central-1.amazonaws.com',
}


def _lease(kind: BackendKind, auth: AuthMethod, **fields: object) -> BackendLease:
    return BackendLease.model_validate(
        {
            'lease_id': 'l',
            'backend_id': 'b',
            'base_url': _ADDRESS[kind],
            'model': 'claude',
            'kind': kind,
            'auth': auth,
            **fields,
        }
    )


def _credentials() -> CloudCredentials:
    """Bearer tokens tok-1, tok-2, ... for every token kind, and `AWS_KEYS` for Bedrock."""
    return FakeCloud().credentials()


async def _prepare(lease: BackendLease, *, stream: bool = False, asked: CallerHeaders | None = None) -> MessagesCall:
    return await MessagesCalls(_credentials()).prepare(
        lease, request=MessagesRequest(**_BODY, stream=stream), stream=stream, asked=asked or CallerHeaders()
    )


class TestEachKindIsSentWhatItTakes:
    async def test_anthropics_api_gets_the_body_its_key_and_the_callers_version_and_betas(self):
        lease = _lease(BackendKind.ANTHROPIC, AuthMethod.API_KEY, api_key='sk-ant', model_ids={'claude': 'claude-x'})

        call = await _prepare(lease, asked=CallerHeaders(version='2024-01-01', betas=('a', 'b')))

        assert call.url == 'https://api.anthropic.com/v1/messages'
        assert call.headers == {
            'content-type': 'application/json',
            'anthropic-version': '2024-01-01',
            'anthropic-beta': 'a,b',
            'x-api-key': 'sk-ant',
        }
        assert json.loads(call.content) == {**_BODY, 'stream': False, 'model': 'claude-x'}

    async def test_a_caller_naming_no_version_gets_the_default_and_no_betas(self):
        call = await _prepare(_lease(BackendKind.ANTHROPIC, AuthMethod.API_KEY, api_key='k'))
        assert (call.headers['anthropic-version'], 'anthropic-beta' in call.headers) == (ANTHROPIC_VERSION, False)

    async def test_foundry_with_a_managed_identity_is_sent_the_token_and_no_key(self):
        lease = _lease(BackendKind.AZURE_ANTHROPIC, AuthMethod.MANAGED_IDENTITY, auth_settings={'client_id': 'c'})

        call = await _prepare(lease)

        assert call.url == 'https://res.services.ai.azure.com/anthropic/v1/messages'
        assert call.headers == {
            'content-type': 'application/json',
            'anthropic-version': ANTHROPIC_VERSION,
            'authorization': 'Bearer tok-1',
        }

    @pytest.mark.parametrize(('stream', 'action'), [(False, 'rawPredict'), (True, 'streamRawPredict')])
    async def test_vertex_names_the_model_in_the_address_and_the_version_in_the_body(self, stream, action):
        lease = _lease(BackendKind.VERTEX_ANTHROPIC, AuthMethod.AMBIENT, model_ids={'claude': 'claude-x@2026'})

        call = await _prepare(lease, stream=stream, asked=CallerHeaders(betas=('b',)))

        assert call.url == f'{_ADDRESS[BackendKind.VERTEX_ANTHROPIC]}/claude-x@2026:{action}'
        assert call.headers == {
            'content-type': 'application/json',
            'authorization': 'Bearer tok-1',
            'anthropic-beta': 'b',
        }
        assert json.loads(call.content) == {
            'max_tokens': 64,
            'messages': [{'role': 'user', 'content': 'hi'}],
            'stream': stream,
            'anthropic_version': VERTEX_VERSION,
        }

    @pytest.mark.parametrize(
        ('stream', 'action', 'accept'),
        [
            (False, 'invoke', 'application/json'),
            (True, 'invoke-with-response-stream', 'application/vnd.amazon.eventstream'),
        ],
    )
    async def test_bedrock_names_the_model_in_the_address_and_takes_version_and_betas_in_the_body(
        self, stream, action, accept
    ):
        lease = _lease(
            BackendKind.BEDROCK,
            AuthMethod.ASSUME_ROLE,
            region='eu-central-1',
            auth_settings={'role_arn': 'arn:aws:iam::1:role/r'},
            model_ids={'claude': 'arn:aws:bedrock:eu-central-1:1:inference-profile/eu.claude'},
        )

        call = await _prepare(lease, stream=stream, asked=CallerHeaders(betas=('b',)))

        encoded = 'arn%3Aaws%3Abedrock%3Aeu-central-1%3A1%3Ainference-profile%2Feu.claude'
        assert call.url == f'https://bedrock-runtime.eu-central-1.amazonaws.com/model/{encoded}/{action}'
        assert call.headers['accept'] == accept
        assert 'anthropic-beta' not in call.headers
        assert json.loads(call.content) == {
            'max_tokens': 64,
            'messages': [{'role': 'user', 'content': 'hi'}],
            'anthropic_version': BEDROCK_VERSION,
            'anthropic_beta': ['b'],
        }

    async def test_bedrock_is_signed_as_botocore_would_sign_that_request_for_that_region(self, monkeypatch):
        """The expectation is computed here with botocore's own signer, not with the code under test."""
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

        call = await _prepare(lease)

        expected = AWSRequest(
            method='POST',
            url=call.url,
            data=call.content,
            headers={'content-type': 'application/json', 'accept': 'application/json'},
        )
        SigV4Auth(Credentials('AKIDEXAMPLE', 'SECRET-MATERIAL', 'SESSION'), 'bedrock', 'eu-central-1').add_auth(
            expected
        )
        assert call.headers['Authorization'] == expected.headers['Authorization']
        assert call.headers['Authorization'].startswith(
            'AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20261114/eu-central-1/bedrock/aws4_request'
        )
        assert call.headers['X-Amz-Security-Token'] == 'SESSION'
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


def _exception(name: str) -> bytes:
    return _event_stream_message({':message-type': 'exception', ':exception-type': name}, b'{"message": "x"}')


_START = {'type': 'message_start', 'message': {'usage': {'input_tokens': 5, 'cache_read_input_tokens': 100}}}
_DELTA = {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'Hello'}}
_END = {'type': 'message_delta', 'usage': {'output_tokens': 7}}


_BEDROCK = _lease(BackendKind.BEDROCK, AuthMethod.AMBIENT, region='eu-central-1')


async def _bedrock_stream(content: bytes) -> list:
    upstream = MessagesUpstream(
        credentials=_credentials(), client_factory=ScriptedProvider(httpx.Response(200, content=content))
    )
    return [event async for event in upstream.stream(lease=_BEDROCK, request=_REQUEST, asked=CallerHeaders())]


class TestBedrocksStreamComesBackAsAnthropicsEvents:
    async def test_each_chunk_is_the_event_it_carries_as_the_sse_frame_anthropic_would_send(self):
        events = await _bedrock_stream(_chunk(_START) + _chunk(_DELTA) + _chunk(_END))

        assert [event.frame for event in events] == [
            f'event: {e["type"]}\ndata: {json.dumps(e)}\n\n' for e in (_START, _DELTA, _END)
        ]
        assert [event.event for event in events] == [event_of(e) for e in (_START, _DELTA, _END)]

    @pytest.mark.parametrize(
        ('exception', 'status'),
        [('throttlingException', 429), ('validationException', 400), ('somethingNew', 502)],
    )
    async def test_an_exception_event_is_the_http_failure_it_stands_for(self, exception, status):
        with pytest.raises(UpstreamError) as raised:
            await _bedrock_stream(_chunk(_START) + _exception(exception))
        assert raised.value.status_code == status

    async def test_an_event_that_is_not_a_chunk_says_nothing(self):
        initial = _event_stream_message({':message-type': 'event', ':event-type': 'initial-response'}, b'{}')
        events = await _bedrock_stream(initial + _chunk(_END))
        assert [event.event for event in events] == [event_of(_END)]


class TestTheProvidersCountIsWhatIsBilled:
    def test_the_prompt_is_its_three_parts_together(self):
        usage = MessagesUsage.of(
            AnthropicUsage(input_tokens=5, cache_read_input_tokens=100, cache_creation_input_tokens=20, output_tokens=7)
        )
        assert (usage.input_tokens, usage.cache_read, usage.cache_write, usage.output, usage.total_tokens) == (
            125,
            100,
            20,
            7,
            132,
        )

    def test_a_stream_reads_the_prompt_from_its_start_and_the_output_from_its_last_delta(self):
        tally = StreamTally()
        for event in (_START, _DELTA, {'type': 'message_delta', 'usage': {'output_tokens': 3}}, _END, {'type': 'ping'}):
            parsed = event_of(event)
            assert parsed is not None
            tally.absorb(parsed)
        assert (tally.usage.input_tokens, tally.usage.cache_read, tally.usage.output, tally.text) == (
            105,
            100,
            7,
            ['Hello'],
        )

    def test_a_failed_attempt_is_not_billed(self):
        tally = StreamTally()
        start = event_of(_START)
        assert start is not None
        tally.absorb(start)
        tally.start_over()
        assert (tally.usage, tally.text) == (MessagesUsage(), [])


class TestARefusedCredentialIsRenewedOnce:
    @pytest.mark.parametrize('status', [401, 403])
    async def test_a_token_kind_is_retried_once_with_a_fresh_token(self, status):
        message = {'type': 'message', 'content': [], 'usage': {'input_tokens': 1, 'output_tokens': 1}}
        provider = ScriptedProvider.refusing(1, httpx.Response(200, json=message), status=status)
        upstream = MessagesUpstream(credentials=_credentials(), client_factory=provider)

        answer = await upstream.complete(
            lease=_lease(BackendKind.VERTEX_ANTHROPIC, AuthMethod.AMBIENT), request=_REQUEST, asked=CallerHeaders()
        )

        assert answer.sent == message
        assert answer.message.usage == AnthropicUsage(input_tokens=1, output_tokens=1)
        assert provider.authorizations() == ['Bearer tok-1', 'Bearer tok-2']

    async def test_a_key_refused_is_not_asked_again(self):
        provider = ScriptedProvider.refusing(1, httpx.Response(200, json={}))
        upstream = MessagesUpstream(credentials=_credentials(), client_factory=provider)

        with pytest.raises(UpstreamError) as raised:
            await upstream.complete(
                lease=_lease(BackendKind.ANTHROPIC, AuthMethod.API_KEY, api_key='k'),
                request=_REQUEST,
                asked=CallerHeaders(),
            )

        assert raised.value.status_code == 401
        assert [request.headers['x-api-key'] for request in provider.sent] == ['k']
