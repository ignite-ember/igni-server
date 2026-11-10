"""Tests for POST /chat/completions — body.model honoured, 404 on unknown model.

The chat endpoint forwards to an upstream provider. We only exercise the
fail-fast paths (model selection + 404) here so we don't need to mock
httpx or the concurrency gate.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.schemas import BackendLease, ModelNotAvailableError
from tests.conftest import auth_header, create_org, create_user, make_token


def _make_pool(acquire_side_effect=None, acquire_return=None):
    """Return a mock pool with a spy acquire."""
    pool = AsyncMock()
    if acquire_side_effect is not None:
        pool.acquire = AsyncMock(side_effect=acquire_side_effect)
    else:
        result = acquire_return or BackendLease(
            lease_id='l1',
            backend_id='gpu-01',
            api_key=None,
            base_url='http://gpu-01.internal:8000/v1',
            model='from-pool-default',
        )
        pool.acquire = AsyncMock(return_value=result)
    return pool


@pytest.fixture
async def authed(client, db):
    await create_user(db, user_id='chat-user', email='chat@test.com')
    org = await create_org(db, owner_user_id='chat-user', slug='chat-org', display_name='Chat')
    token = make_token('chat-user', org_id=str(org.id))
    return client, token


class TestModelNotAvailableReturns404:
    @pytest.mark.asyncio
    async def test_returns_404_when_model_unknown(self, authed):
        client, token = authed
        pool = _make_pool(
            acquire_side_effect=ModelNotAvailableError(
                model='gpt-bogus', available=['claude-opus-4-7', 'claude-haiku-4-5']
            )
        )

        def fake_get_pool(_self):
            return pool

        with patch(
            'app.services.ai.pool_manager.PoolManager.get_pool',
            new=fake_get_pool,
        ):
            resp = await client.post(
                '/chat/completions',
                headers=auth_header(token),
                json={'model': 'gpt-bogus', 'messages': [{'role': 'user', 'content': 'hi'}]},
            )

        assert resp.status_code == 404
        detail = resp.json()['detail']
        assert 'gpt-bogus' in detail
        # The error advertises the available models so the CLI can suggest a fix.
        assert 'claude-opus-4-7' in detail


class TestAcquireKeyReceivesModel:
    @pytest.mark.asyncio
    async def test_body_model_passed_to_acquire(self, authed):
        client, token = authed
        # Raise ModelNotAvailableError so we abort BEFORE the upstream
        # call (we don't need to mock httpx). The acquire spy will
        # have captured the model argument before raising.
        pool = _make_pool(acquire_side_effect=ModelNotAvailableError(model='claude-opus-4-7', available=[]))

        def fake_get_pool(_self):
            return pool

        with patch(
            'app.services.ai.pool_manager.PoolManager.get_pool',
            new=fake_get_pool,
        ):
            await client.post(
                '/chat/completions',
                headers=auth_header(token),
                json={'model': 'claude-opus-4-7', 'messages': [{'role': 'user', 'content': 'hi'}]},
            )

        pool.acquire.assert_awaited_once()
        call = pool.acquire.await_args
        assert call.kwargs.get('model') == 'claude-opus-4-7'

    @pytest.mark.asyncio
    async def test_omitted_model_passed_as_none(self, authed):
        """When body.model is empty / missing, acquire receives model=None
        and the pool is free to pick whatever key it wants (legacy behaviour)."""
        client, token = authed
        pool = _make_pool(acquire_side_effect=ModelNotAvailableError(model='', available=[]))

        def fake_get_pool(_self):
            return pool

        with patch(
            'app.services.ai.pool_manager.PoolManager.get_pool',
            new=fake_get_pool,
        ):
            # Send no model field at all.
            await client.post(
                '/chat/completions',
                headers=auth_header(token),
                json={'messages': [{'role': 'user', 'content': 'hi'}]},
            )

        pool.acquire.assert_awaited_once()
        call = pool.acquire.await_args
        assert call.kwargs.get('model') is None
