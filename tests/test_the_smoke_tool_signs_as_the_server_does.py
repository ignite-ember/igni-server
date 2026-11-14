"""`tools/smoke_provider.py` proves a backend by sending one request exactly as the server would.

It is a hand-run tool that reaches outside on purpose, so nothing here
runs it against a cloud: the provider is a `httpx.MockTransport` and the
cloud's credentials are `FakeCloud`'s. What is held here is that it sends
what the server sends — each kind's own address, signature and API — and
reports the provider's count, or the failure, in the words it prints.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import re
from types import ModuleType

import httpx
import pytest

from app.services.ai.backend import InferenceBackend
from app.services.ai.backend_provider import BackendConfigError, InferenceBackendProvider
from tests.cloud_fakes import backend, FakeCloud, ScriptedProvider

_TOOL = pathlib.Path(__file__).resolve().parent.parent / 'tools' / 'smoke_provider.py'

_NODE = backend(id='gpu-1', base_url='http://node.invalid/v1', models=['qwen'], auth='api_key', api_key='sk-node')
_ANTHROPIC = backend(id='anthropic', kind='anthropic', models=['claude'], api_key='sk-ant')
_BEDROCK = backend(
    id='bedrock-eu',
    kind='bedrock',
    region='eu-central-1',
    models=['claude'],
    model_ids={'claude': 'eu.anthropic.claude-v1:0'},
    auth='ambient',
)

_COMPLETION = {
    'id': 'c',
    'object': 'chat.completion',
    'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'ready'}, 'finish_reason': 'stop'}],
    'usage': {'prompt_tokens': 9, 'completion_tokens': 1, 'total_tokens': 10},
}
_MESSAGE = {
    'type': 'message',
    'content': [{'type': 'text', 'text': 'ready'}],
    'usage': {'input_tokens': 9, 'cache_read_input_tokens': 4, 'output_tokens': 1},
}


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location('smoke_provider', _TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def deployed(monkeypatch):
    """This deployment's backends are exactly *backends*."""

    def deploy(*backends: InferenceBackend) -> None:
        async def load(self):
            return list(backends)

        monkeypatch.setattr(InferenceBackendProvider, '__init__', lambda self: None)
        monkeypatch.setattr(InferenceBackendProvider, 'load', load)

    return deploy


async def _run(argv: list[str], provider: ScriptedProvider, capsys) -> tuple[int, list[str]]:
    code = await _tool().main(argv, credentials=FakeCloud().credentials(), client_factory=provider)
    printed = re.sub(r'\d+\.\d+s', 'Ns', capsys.readouterr().out)
    return code, printed.splitlines()


@pytest.mark.parametrize(
    ('defined', 'answer', 'url', 'printed'),
    [
        pytest.param(
            _NODE,
            _COMPLETION,
            'http://node.invalid/v1/chat/completions',
            ['gpu-1: openai, api_key auth, http://node.invalid/v1', "asking for 'qwen'", 'OK in Ns — tokens: 10'],
            id='a GPU node, on its OpenAI API',
        ),
        pytest.param(
            _ANTHROPIC,
            _MESSAGE,
            'https://api.anthropic.com/v1/messages',
            [
                'anthropic: anthropic, api_key auth, https://api.anthropic.com',
                "asking for 'claude'",
                'OK in Ns — tokens: input 13 (cache read 4, written 0), output 1',
            ],
            id="Anthropic's own API",
        ),
        pytest.param(
            _BEDROCK,
            _MESSAGE,
            'https://bedrock-runtime.eu-central-1.amazonaws.com/model/eu.anthropic.claude-v1%3A0/invoke',
            [
                'bedrock-eu: bedrock, ambient auth, https://bedrock-runtime.eu-central-1.amazonaws.com',
                "asking for 'claude' (sent as 'eu.anthropic.claude-v1:0')",
                'OK in Ns — tokens: input 13 (cache read 4, written 0), output 1',
            ],
            id='Claude on Bedrock, signed with the server role',
        ),
    ],
)
async def test_a_backend_is_called_on_its_own_api_and_its_count_printed(
    deployed, capsys, defined, answer, url, printed
):
    deployed(defined)
    provider = ScriptedProvider(httpx.Response(200, json=answer))

    assert await _run([defined.id], provider, capsys) == (0, printed)
    assert [str(request.url) for request in provider.sent] == [url]


@pytest.mark.parametrize(
    ('defined', 'answer', 'url', 'outcome'),
    [
        pytest.param(
            _NODE,
            httpx.Response(200, text='data: {"choices": []}\n\ndata: [DONE]\n\n'),
            'http://node.invalid/v1/chat/completions',
            'OK in Ns — 2 stream lines',
            id='a GPU node',
        ),
        pytest.param(
            _ANTHROPIC,
            httpx.Response(
                200,
                text='event: message_start\ndata: {"type": "message_start", "message": {"usage": {"input_tokens": 9}}}'
                '\n\nevent: message_delta\ndata: {"type": "message_delta", "usage": {"output_tokens": 1}}\n\n',
            ),
            'https://api.anthropic.com/v1/messages',
            'OK in Ns — tokens: input 9 (cache read 0, written 0), output 1',
            id="Anthropic's own API",
        ),
    ],
)
async def test_a_streamed_request_is_streamed_as_igni_clients_do(deployed, capsys, defined, answer, url, outcome):
    deployed(defined)
    provider = ScriptedProvider(answer)

    code, printed = await _run([defined.id, '--stream'], provider, capsys)

    assert (code, printed[1:]) == (0, [f'asking for {defined.models[0]!r}; streamed', outcome])
    assert [str(request.url) for request in provider.sent] == [url]
    assert json.loads(provider.sent[0].content)['stream'] is True


async def test_bedrock_is_signed_with_the_servers_keys_and_never_prints_them(deployed, capsys):
    deployed(_BEDROCK)
    provider = ScriptedProvider(httpx.Response(200, json=_MESSAGE))

    _code, printed = await _run(['bedrock-eu'], provider, capsys)

    assert provider.sent[0].headers['authorization'].startswith('AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/'), (
        'signed with SigV4, with the keys the server holds'
    )
    assert provider.sent[0].headers['x-amz-security-token'] == 'SESSION'
    assert not any('SECRET-MATERIAL' in line or 'SESSION' in line for line in printed)


async def test_the_prompt_is_the_short_one_and_the_answer_is_kept_small(deployed, capsys):
    deployed(_ANTHROPIC)
    provider = ScriptedProvider(httpx.Response(200, json=_MESSAGE))

    await _run(['anthropic'], provider, capsys)

    assert json.loads(provider.sent[0].content) == {
        'model': 'claude',
        'max_tokens': 16,
        'messages': [{'role': 'user', 'content': 'Reply with the single word: ready'}],
        'stream': False,
    }


async def test_a_refused_identity_is_a_failure_that_says_where_to_look(deployed, capsys):
    deployed(_BEDROCK)
    provider = ScriptedProvider(httpx.Response(403, json={'message': 'not authorized'}))

    code, printed = await _run(['bedrock-eu'], provider, capsys)

    assert code == 1
    assert printed[2:] == [
        'FAILED after Ns: 403 — The inference node refused the request (403).',
        "The server log has the provider's own words; a 401 or 403 is the identity, a 404 the model name.",
    ]
    assert [request.headers['authorization'][:40] for request in provider.sent] == [
        'AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/',
        'AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/',
    ], 'the refused credential is renewed once, as the server does'


async def test_an_unknown_backend_is_named_and_nothing_is_sent(deployed, capsys):
    deployed(_BEDROCK, _ANTHROPIC)
    provider = ScriptedProvider(httpx.Response(200, json=_MESSAGE))

    assert await _run(['no-such-backend'], provider, capsys) == (
        2,
        ["No backend 'no-such-backend'. This deployment has: anthropic, bedrock-eu"],
    )
    assert provider.sent == []


async def test_a_model_the_backend_does_not_serve_is_refused_before_sending(deployed, capsys):
    deployed(_BEDROCK)
    provider = ScriptedProvider(httpx.Response(200, json=_MESSAGE))

    assert await _run(['bedrock-eu', '--model', 'not-served'], provider, capsys) == (
        2,
        ["Backend 'bedrock-eu' does not serve 'not-served'; it serves claude"],
    )
    assert provider.sent == []


async def test_a_deployment_with_no_backends_says_so_in_its_own_words(monkeypatch, capsys):
    async def load(self):
        raise BackendConfigError('No inference backends are available.')

    monkeypatch.setattr(InferenceBackendProvider, '__init__', lambda self: None)
    monkeypatch.setattr(InferenceBackendProvider, 'load', load)
    provider = ScriptedProvider(httpx.Response(200, json=_MESSAGE))

    assert await _run(['bedrock-eu'], provider, capsys) == (2, ['No inference backends are available.'])
    assert provider.sent == []
