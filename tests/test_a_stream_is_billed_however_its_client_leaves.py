"""A streamed completion is billed once, and its node slot comes back, however the client leaves.

Found by the real-model rerun: the OpenAI SDK, like `httpx`, closes the
connection as soon as it has read `data: [DONE]`. The stream's tail —
the usage write, then the lease release — was still running then, in the
response's task, and Starlette cancels that task when the client goes
(`StreamingResponse` listens for the disconnect; `BaseHTTPMiddleware`'s
task group carries the cancellation). The usage write was cancelled
mid-query ("Cancelled via cancel scope" in the API log, 23 streams and
22 usage rows), and on the code before the release moved into a
`finally`, the release went with it: the run's only node was blocked
for the full 900-second lease timeout.

A client that leaves mid-stream recorded no usage at all, and the
release in the generator's `finally` ran under the same cancellation.

These run the real chat route under a real uvicorn behind two
`BaseHTTPMiddleware`s, like the app's stack, with an `httpx` client. The
usage write and the release both await, as the database and Redis do, so
a cancellation that reaches them takes them with it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
import httpx
import pytest
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn

from app.api.base_v1.endpoints.client import chat as chat_module

USAGE = {'prompt_tokens': 23, 'completion_tokens': 69, 'total_tokens': 92}


@dataclass
class _Lease:
    lease_id: str = 'lease-1'
    backend_id: str = 'node-1'
    base_url: str = 'http://node.invalid'
    model: str = 'fake-model'
    api_key: str | None = None


@dataclass
class _Pool:
    """A node pool whose release takes a moment, as a Redis call does."""

    released: list[str] = field(default_factory=list)

    async def acquire(self, user_id, *, model=None, exclude_backend_id=None):
        return _Lease()

    async def release(self, lease):
        await asyncio.sleep(0.05)
        self.released.append(lease.lease_id)


@dataclass
class _UsageDB:
    """A usage table whose write takes a moment, as a database insert does."""

    rows: list[dict] = field(default_factory=list)
    started: int = 0

    async def log(self, **row):
        self.started += 1
        await asyncio.sleep(0.3)
        self.rows.append(row)


def _chunk(content: str) -> dict:
    return {'id': 'c', 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {'content': content}}]}


@dataclass
class _Node:
    """The upstream stream: some chunks, then either the end or silence."""

    hangs_after: int | None = None
    closed: bool = False

    async def stream(self, url, headers, body):
        try:
            for i, word in enumerate(['one ', 'two ', 'three']):
                if self.hangs_after is not None and i == self.hangs_after:
                    await asyncio.sleep(60)
                chunk = _chunk(word)
                yield f'data: {json.dumps(chunk)}\n\n', chunk
            final = {'id': 'c', 'object': 'chat.completion.chunk', 'choices': [], 'usage': USAGE}
            yield f'data: {json.dumps(final)}\n\n', final
            yield 'data: [DONE]\n\n', None
        finally:
            self.closed = True


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class _PassThrough(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        return await call_next(request)


@pytest.fixture
def server(monkeypatch):
    """The chat route under uvicorn; yields (base url, pool, usage table, node)."""
    pool, usage, node = _Pool(), _UsageDB(), _Node()
    monkeypatch.setattr(chat_module.Producers, 'machine', AsyncMock(return_value=None))
    monkeypatch.setattr(chat_module.AuthService, 'resolve_user_org', AsyncMock(return_value=('ada', 'org-1')))
    monkeypatch.setattr(chat_module.GroupsService, 'model_for_user', AsyncMock(return_value=None))
    monkeypatch.setattr(chat_module, 'PoolManager', lambda: SimpleNamespace(get_pool=lambda: pool))
    monkeypatch.setattr(chat_module, '_usage_db', usage)
    monkeypatch.setattr(chat_module, '_stream_upstream', node.stream)

    app = FastAPI()
    app.add_middleware(_PassThrough)
    app.add_middleware(_PassThrough)
    app.include_router(chat_module.router)
    return app, pool, usage, node


async def _serving(app):
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning', lifespan='off'))
    task = asyncio.ensure_future(srv.serve())
    for _ in range(200):
        if srv.started:
            break
        await asyncio.sleep(0.01)
    assert srv.started
    return f'http://127.0.0.1:{port}', srv, task


async def _settled(pool: _Pool, usage: _UsageDB) -> None:
    """Wait for the tail to finish, and a little longer to catch a second write."""
    for _ in range(200):
        if pool.released and usage.rows:
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.5)


_REQUEST = {'model': 'fake-model', 'stream': True, 'messages': [{'role': 'user', 'content': 'Count to three.'}]}


async def _run(app, read):
    url, srv, task = await _serving(app)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            async with client.stream(
                'POST', f'{url}/chat/completions', json=_REQUEST, headers={'Authorization': 'Bearer t'}
            ) as resp:
                assert resp.status_code == 200
                await read(resp)
    finally:
        srv.should_exit = True
        await asyncio.wait_for(task, 10)


async def test_a_client_that_closes_right_after_done_is_billed_once_and_frees_the_node(server):
    """The OpenAI SDK's shape, and the rerun's finding."""
    app, pool, usage, node = server

    async def until_done(resp):
        async for line in resp.aiter_lines():
            if line == 'data: [DONE]':
                return  # and close the connection, as the SDK does

    await _run(app, until_done)
    await _settled(pool, usage)

    assert usage.started == 1
    assert len(usage.rows) == 1, f'usage written {len(usage.rows)} times'
    row = usage.rows[0]
    assert (row['input_tokens'], row['output_tokens'], row['total_tokens']) == (23, 69, 92)
    assert row['stream'] is True
    assert pool.released == ['lease-1']


async def test_a_client_that_reads_to_the_end_is_billed_once_and_frees_the_node(server):
    app, pool, usage, node = server

    async def everything(resp):
        async for _ in resp.aiter_lines():
            pass

    await _run(app, everything)
    await _settled(pool, usage)

    assert len(usage.rows) == 1
    assert usage.rows[0]['total_tokens'] == 92
    assert pool.released == ['lease-1']


async def test_a_client_that_leaves_mid_stream_is_billed_what_it_used_and_frees_the_node(server):
    """The upstream goes quiet after the first chunk and the client
    leaves. The prompt's prefill ran and one chunk came back: that much
    is recorded, estimated as a stream without a usage block is, and the
    upstream read is closed."""
    app, pool, usage, node = server
    node.hangs_after = 1

    async def first_chunk(resp):
        async for line in resp.aiter_lines():
            if line.startswith('data: '):
                return

    await _run(app, first_chunk)
    await _settled(pool, usage)

    assert len(usage.rows) == 1, f'usage written {len(usage.rows)} times'
    expected = chat_module.TokenEstimator().estimate(body={**_REQUEST}, output_pieces=['one '])
    row = usage.rows[0]
    assert row['input_tokens'] > 0 and row['output_tokens'] > 0
    assert row['total_tokens'] == row['input_tokens'] + row['output_tokens']
    assert row['input_tokens'] == pytest.approx(expected[0], abs=8)
    assert row['stream'] is True
    assert pool.released == ['lease-1']
    assert node.closed
