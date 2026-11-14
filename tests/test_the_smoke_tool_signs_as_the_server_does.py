"""`tools/smoke_provider.py` proves a cloud backend by sending as the server would.

It is a hand-run tool that reaches outside on purpose, so nothing here
runs it against a cloud. What can be held here is that it would send
what the server sends: the lease it builds is the one the pool builds —
kind, region, auth method, settings, secret, model names — so a pass
from the tool means a user's request would pass.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

from app.services.ai.backend_pool import InferenceBackendPool
from app.services.ai.backend_provider import InferenceBackendProvider
from app.services.ai.schemas import InferenceBackend
from tests.test_inference_backend_pool import FakeRedis, FakeRegistry

_TOOL = pathlib.Path(__file__).resolve().parent.parent / 'tools' / 'smoke_provider.py'


def _tool():
    spec = importlib.util.spec_from_file_location('smoke_provider', _TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BEDROCK = InferenceBackend(
    id='bedrock-eu',
    kind='bedrock',
    region='eu-central-1',
    models=['claude'],
    model_ids={'claude': 'eu.anthropic.claude-v1:0'},
    auth='assume_role',
    auth_settings={'role_arn': 'arn:aws:iam::1:role/r', 'external_id': 'x'},
)


async def test_the_lease_it_builds_is_the_one_the_pool_builds(tmp_path):
    path = tmp_path / 'backends.json'
    path.write_text(json.dumps([_BEDROCK.model_dump(mode='json')]))
    pool = InferenceBackendPool(provider=InferenceBackendProvider(path=str(path), registry=FakeRegistry()))
    redis = FakeRedis()

    async def _redis():
        return redis

    pool._get_redis = _redis  # type: ignore[method-assign]

    pooled = await pool.acquire('u', model='claude', wire=_BEDROCK.wire)
    built = _tool().lease_of(_BEDROCK, 'claude')

    assert built.model_dump(exclude={'lease_id'}) == pooled.model_dump(exclude={'lease_id'})


async def test_an_unknown_backend_is_named_and_nothing_is_sent(monkeypatch, capsys):
    tool = _tool()

    async def load(self):
        return [_BEDROCK]

    monkeypatch.setattr(InferenceBackendProvider, 'load', load)
    monkeypatch.setattr(InferenceBackendProvider, '__init__', lambda self: None)

    assert await tool.main(['no-such-backend']) == 2
    assert 'bedrock-eu' in capsys.readouterr().out


@pytest.mark.parametrize('model', ['not-served'])
async def test_a_model_the_backend_does_not_serve_is_refused_before_sending(monkeypatch, capsys, model):
    tool = _tool()

    async def load(self):
        return [_BEDROCK]

    monkeypatch.setattr(InferenceBackendProvider, 'load', load)
    monkeypatch.setattr(InferenceBackendProvider, '__init__', lambda self: None)

    assert await tool.main(['bedrock-eu', '--model', model]) == 2
    assert 'does not serve' in capsys.readouterr().out
