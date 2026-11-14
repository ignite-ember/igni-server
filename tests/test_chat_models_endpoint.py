"""Tests for GET /chat/models — model discovery endpoint for igni."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.ai.backend import InferenceBackend
from app.services.ai.chat.services import chat_services, ChatServices
from tests.backend_pool_fakes import pool_over
from tests.conftest import auth_header, create_org, create_user, make_token


async def _nothing() -> dict:
    return {}


def _cfg(key_id: str, model: str, base_url: str = 'http://gpu-01.internal:8000/v1') -> InferenceBackend:
    return InferenceBackend(
        id=key_id,
        api_key=None,
        base_url=base_url,
        model=model,
    )


@pytest.fixture(autouse=True)
def _fresh_catalog(app):
    """A fresh `ChatServices` per test, so the 60s model cache never carries over."""
    services = ChatServices()
    app.dependency_overrides[chat_services] = lambda: services
    yield
    app.dependency_overrides.pop(chat_services, None)


class TestListChatModels:
    async def test_unauthenticated_rejected(self, client):
        resp = await client.get('/chat/models')
        assert resp.status_code == 401

    async def test_returns_each_model_once_with_the_api_that_serves_it(self, client, db, tmp_path):
        await create_user(db, user_id='u1', email='u1@test.com')
        org = await create_org(db, owner_user_id='u1', slug='org-a', display_name='Org A')
        token = make_token('u1', org_id=str(org.id))

        pool, _redis = pool_over(
            [
                _cfg('opus-1', 'claude-opus-4-7'),
                _cfg('opus-2', 'claude-opus-4-7'),
                _cfg('haiku-1', 'claude-haiku-4-5'),
                InferenceBackend(id='anthropic', kind='anthropic', api_key='k', models=['claude-opus-5-5']),
            ],
            tmp_path,
        )

        with patch('app.services.ai.pool_manager.PoolManager.get_pool', new=lambda _self: pool):
            resp = await client.get('/chat/models', headers=auth_header(token))

        assert resp.status_code == 200
        body = resp.json()
        # Each model once, in the order the pool first names it, with the API it is served on.
        assert body == {
            'models': [
                {'id': 'claude-opus-4-7', 'api': 'openai'},
                {'id': 'claude-haiku-4-5', 'api': 'openai'},
                {'id': 'claude-opus-5-5', 'api': 'anthropic'},
            ]
        }
        # ``base_url`` is deliberately not returned — the CLI always
        # talks to the igni Server proxy, so upstream provider URLs
        # are routing internals the client doesn't need to see.
        for m in body['models']:
            assert 'base_url' not in m

    async def test_empty_pool_returns_empty_list(self, client, db, tmp_path):
        await create_user(db, user_id='u-empty', email='empty@test.com')
        org = await create_org(db, owner_user_id='u-empty', slug='empty-org', display_name='Empty')
        token = make_token('u-empty', org_id=str(org.id))

        empty = SimpleNamespace(models_by_wire=lambda: _nothing())

        with patch('app.services.ai.pool_manager.PoolManager.get_pool', new=lambda _self: empty):
            resp = await client.get('/chat/models', headers=auth_header(token))

        assert resp.status_code == 200
        assert resp.json() == {'models': []}
