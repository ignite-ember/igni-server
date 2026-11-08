"""What `/chat/completions` answers, word for word, when it cannot admit the request, and how a stream is sent.

The refusals moved out of the route into `ChatAdmission` and the
`ChatRefused` family; their statuses were pinned, these pin the
sentences and headers the caller reads. A stream's response is
`StreamThatEnds`: its media type and headers are what keep a proxy from
buffering the answer.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import settings
from app.services.ai.backend_provider import BackendConfigError
from app.services.ai.chat.services import chat_services, ChatServices
from app.services.ai.fair_queue import FairQueue
from app.services.ai.schemas import NoBackendAvailableError
from tests.chat_proxy import NO_BACKOFF, on_transport, SpyPool, sse, UsageRows
from tests.conftest import auth_header, create_org, create_user, make_token

_CHAT = '/chat/completions'


@pytest.fixture
async def headers(db):
    await create_user(db, user_id='words-user', email='words@test.com')
    org = await create_org(db, owner_user_id='words-user', slug='words-org', display_name='W')
    return auth_header(make_token('words-user', org_id=str(org.id)))


@pytest.fixture
def serve(app):
    """Serve the chat routes from *services*."""

    def install(services: ChatServices) -> None:
        app.dependency_overrides[chat_services] = lambda: services

    yield install
    app.dependency_overrides.pop(chat_services, None)


def _pool_refusing(error: Exception):
    pool = AsyncMock()
    pool.acquire = AsyncMock(side_effect=error)
    return pool


def _with_pool(pool):
    return patch('app.services.ai.pool_manager.PoolManager.get_pool', new=lambda _self: pool)


def _body(**extra) -> dict:
    return {'messages': [{'role': 'user', 'content': 'hi'}], **extra}


class TestTheRefusalsSayWhy:
    async def test_a_full_queue_is_a_429_that_says_so(self, client, headers, serve):
        serve(ChatServices(queue=FairQueue(max_length=0), usage_db=UsageRows()))

        with _with_pool(_pool_refusing(NoBackendAvailableError())):
            response = await client.post(_CHAT, json=_body(), headers=headers)

        assert response.status_code == 429
        assert response.json()['error'] == {
            'message': 'Every inference node is at capacity and the queue is full. Please retry shortly.',
            'type': 'rate_limit_error',
            'code': None,
            'param': None,
        }
        assert response.headers['Retry-After'] == '30'

    async def test_a_wait_that_runs_out_is_a_429_that_says_so(self, client, headers, serve, monkeypatch):
        monkeypatch.setattr(settings, 'chat_queue_max_wait_seconds', 0.05)
        serve(ChatServices(usage_db=UsageRows()))

        with _with_pool(_pool_refusing(NoBackendAvailableError())):
            response = await client.post(_CHAT, json=_body(), headers=headers)

        assert response.status_code == 429
        assert response.json()['detail'] == 'Waited the longest a request may for a free inference node. Please retry.'
        assert response.headers['Retry-After'] == '30'

    async def test_a_misconfigured_pool_is_a_503_in_its_own_words(self, client, headers, serve):
        serve(ChatServices(usage_db=UsageRows()))

        with _with_pool(_pool_refusing(BackendConfigError('INFERENCE_BACKENDS is not valid JSON'))):
            response = await client.post(_CHAT, json=_body(), headers=headers)

        assert response.status_code == 503
        assert response.json()['detail'] == 'INFERENCE_BACKENDS is not valid JSON'
        assert 'Retry-After' not in response.headers


class TestAStreamIsSentAsItIsMade:
    async def test_it_is_an_event_stream_no_proxy_buffers(self, client, headers, serve):
        def node(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=f'{sse("hel")}\n\n{sse("lo")}\n\ndata: [DONE]\n\n')

        pool = SpyPool()
        serve(ChatServices(upstream=on_transport(node), retry=NO_BACKOFF, usage_db=UsageRows()))

        with _with_pool(pool):
            response = await client.post(_CHAT, json=_body(stream=True), headers=headers)

        assert response.status_code == 200
        assert response.headers['content-type'].startswith('text/event-stream')
        # The app's no-store middleware has the last word on Cache-Control.
        assert response.headers['cache-control'] == 'no-store, max-age=0'
        assert response.headers['connection'] == 'keep-alive'
        assert response.headers['x-accel-buffering'] == 'no'
        assert response.text.endswith('data: [DONE]\n\n')
        assert '"hel"' in response.text and '"lo"' in response.text
        # SpyPool counts a context's starting lease; this one was taken through admission.
        assert pool.released == pool.acquired[1:]
