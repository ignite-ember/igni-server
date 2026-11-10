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

from fastapi import FastAPI
import httpx
import pytest
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn

from app.api.base_v1.endpoints.client import chat as chat_module
from app.api.base_v1.schema.chat import ChatMessage
from app.services.ai.chat.schemas import UpstreamLine
from app.services.ai.chat.services import chat_services, ChatServices
from app.services.ai.token_estimate import TokenEstimator
from app.services.ai.upstream import UpstreamFailure
from tests.chat_proxy import context, lease, person, pools, SpyPool, sse, streamer, UsageRows

USAGE = {'prompt_tokens': 23, 'completion_tokens': 69, 'total_tokens': 92}


@dataclass
class _Pool:
    """A node pool whose release takes a moment, as a Redis call does."""

    released: list[str] = field(default_factory=list)

    async def acquire(self, user_id, *, model=None, exclude_backend_id=None):
        return lease()

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

    async def stream(self, *, lease, request):
        try:
            for i, word in enumerate(['one ', 'two ', 'three']):
                if self.hangs_after is not None and i == self.hangs_after:
                    await asyncio.sleep(60)
                yield UpstreamLine.from_sse(f'data: {json.dumps(_chunk(word))}')
            final = {'id': 'c', 'object': 'chat.completion.chunk', 'choices': [], 'usage': USAGE}
            yield UpstreamLine.from_sse(f'data: {json.dumps(final)}')
            yield UpstreamLine.from_sse('data: [DONE]')
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
def server():
    """The chat route under uvicorn; yields (base url, pool, usage table, node)."""
    pool, usage, node = _Pool(), _UsageDB(), _Node()
    services = ChatServices(pools=pools(pool), usage_db=usage, upstream=node, callers=person())

    app = FastAPI()
    app.add_middleware(_PassThrough)
    app.add_middleware(_PassThrough)
    app.include_router(chat_module.router)
    app.dependency_overrides[chat_services] = lambda: services
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
    expected = TokenEstimator().estimate(
        messages=[ChatMessage.model_validate(m) for m in _REQUEST['messages']], output_pieces=['one ']
    )
    row = usage.rows[0]
    assert row['input_tokens'] > 0 and row['output_tokens'] > 0
    assert row['total_tokens'] == row['input_tokens'] + row['output_tokens']
    assert row['input_tokens'] == pytest.approx(expected.prompt_tokens, abs=8)
    assert row['stream'] is True
    assert pool.released == ['lease-1']
    assert node.closed


class _NodeThatFailsOnClose:
    """A node whose connection errors as it is closed, after the client has gone."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, *, lease, request):
        self.calls += 1
        try:
            for word in ['one ', 'two ', 'three']:
                yield UpstreamLine.from_sse(sse(word))
        finally:
            raise UpstreamFailure.from_transport(httpx.ReadError('connection reset while closing'))


async def test_a_client_that_leaves_while_the_node_fails_to_close_is_still_billed(caplog):
    """Closing the node's read can fail. That failure, raised into a stream
    the client already left, is not an attempt to retry: no retry is logged,
    no second node is asked, and the usage row is still written."""
    pool, usage, node = SpyPool(), UsageRows(), _NodeThatFailsOnClose()
    frames = streamer(node).open(context(pool, usage_db=usage)).frames()

    first = await anext(frames)
    await frames.aclose()

    assert first.startswith('data: ')
    assert len(usage.rows) == 1, f'usage written {len(usage.rows)} times'
    assert usage.rows[0]['stream'] is True
    assert usage.rows[0]['output_tokens'] > 0
    assert pool.released == ['lease-1']
    assert node.calls == 1
    assert 'retrying' not in caplog.text
    assert 'Closing the upstream stream failed' in caplog.text
