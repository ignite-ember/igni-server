"""Tests for GET /chat/models — model discovery endpoint for igni."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.ai.chat.services import chat_services, ChatServices
from app.services.ai.schemas import InferenceBackend
from tests.conftest import auth_header, create_org, create_user, make_token


def _cfg(key_id: str, model: str, base_url: str = 'http://gpu-01.internal:8000/v1') -> InferenceBackend:
    return InferenceBackend(
        id=key_id,
        api_key=None,
        base_url=base_url,
        model=model,
    )


class _FakePool:
    """Stands in for AIKeyPool so we don't hit Redis or Secret Manager."""

    def __init__(self, configs: list[InferenceBackend]) -> None:
        self._configs = configs

    async def list_available_models(self) -> list[tuple[str, str]]:
        seen: dict[tuple[str, str], None] = {}
        for c in self._configs:
            # Every id the node answers to, so a LoRA adapter is
            # advertised as its own selectable model.
            for name in c.models:
                seen.setdefault((name, c.base_url), None)
        return list(seen.keys())


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

    async def test_returns_distinct_models(self, client, db):
        await create_user(db, user_id='u1', email='u1@test.com')
        org = await create_org(db, owner_user_id='u1', slug='org-a', display_name='Org A')
        token = make_token('u1', org_id=str(org.id))

        pool = _FakePool(
            [
                _cfg('opus-1', 'claude-opus-4-7'),
                _cfg('opus-2', 'claude-opus-4-7'),
                _cfg('haiku-1', 'claude-haiku-4-5'),
            ]
        )

        def fake_get_pool(_self):
            return pool

        with patch(
            'app.services.ai.pool_manager.PoolManager.get_pool',
            new=fake_get_pool,
        ):
            resp = await client.get('/chat/models', headers=auth_header(token))

        assert resp.status_code == 200
        body = resp.json()
        # Each model once, in the order the pool first names it.
        assert [m['id'] for m in body['models']] == ['claude-opus-4-7', 'claude-haiku-4-5']
        # ``base_url`` is deliberately not returned — the CLI always
        # talks to the igni Server proxy, so upstream provider URLs
        # are routing internals the client doesn't need to see.
        for m in body['models']:
            assert 'base_url' not in m

    async def test_empty_pool_returns_empty_list(self, client, db):
        await create_user(db, user_id='u-empty', email='empty@test.com')
        org = await create_org(db, owner_user_id='u-empty', slug='empty-org', display_name='Empty')
        token = make_token('u-empty', org_id=str(org.id))

        def fake_get_pool(_self):
            return _FakePool([])

        with patch(
            'app.services.ai.pool_manager.PoolManager.get_pool',
            new=fake_get_pool,
        ):
            resp = await client.get('/chat/models', headers=auth_header(token))

        assert resp.status_code == 200
        assert resp.json() == {'models': []}
