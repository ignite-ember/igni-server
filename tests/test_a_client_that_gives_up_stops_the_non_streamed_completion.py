"""A non-streamed completion is cancelled upstream when its caller leaves.

A streamed response already stops when its client goes: Starlette
closes the generator, which closes the upstream read. A non-streamed
handler is not cancelled on a disconnect — it runs to the end whether
anyone is waiting or not. The igni CLI waits 60 s and then retries,
three times; one slow request ran on the GPU four times and was billed
four times.

`ClientConnection.unless_it_closes` races the work against the connection's
`http.disconnect` and cancels it when the caller has gone. The last test
runs it under a real uvicorn, behind `BaseHTTPMiddleware`s like the
app's own, with a client that times out — the shape of the CLI's
failure. It is also the test that showed polling
`request.is_disconnected()` cannot do this: behind those middlewares it
never reported the disconnect at all.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import socket

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import httpx
import pytest
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn

from app.api.base_v1.endpoints.client import chat as chat_module
from app.api.base_v1.endpoints.client.client_connection import ClientConnection
from app.config import settings
from app.services.ai.chat.services import chat_services, ChatServices
from app.services.ai.fair_queue import FairQueue
from app.services.ai.schemas import NoBackendAvailableError
from app.services.ai.token_estimate import TokenEstimator
from tests.chat_proxy import completions, context, lease, on_transport, person, pools, request, SpyPool, UsageRows

POLL = 0.02


class _Caller:
    """A request whose client leaves after *leaves_after* seconds (never if None).

    `receive` behaves as an ASGI server's does once the body has been
    read: it blocks until the connection closes, then says so.
    """

    def __init__(self, leaves_after: float | None, *, receive_fails: bool = False):
        self._leaves_after = leaves_after
        self._receive_fails = receive_fails

    async def receive(self) -> dict:
        if self._receive_fails:
            raise RuntimeError('receive broke')
        if self._leaves_after is None:
            await asyncio.Event().wait()
        await asyncio.sleep(self._leaves_after)
        return {'type': 'http.disconnect'}


@dataclass
class _Work:
    """Slow work that records whether it finished or was cancelled."""

    seconds: float
    result: object = 'answer'
    cancelled: bool = False
    finished: bool = False

    async def run(self):
        try:
            await asyncio.sleep(self.seconds)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.finished = True
        return self.result


class TestTheRace:
    async def test_a_caller_who_waits_gets_the_answer(self):
        work = _Work(POLL * 3)
        assert await ClientConnection(_Caller(None)).unless_it_closes(work.run()) == 'answer'
        assert work.finished and not work.cancelled

    async def test_a_caller_who_leaves_after_the_answer_changes_nothing(self):
        work = _Work(0)
        assert await ClientConnection(_Caller(POLL * 5)).unless_it_closes(work.run()) == 'answer'
        assert work.finished

    async def test_a_receive_that_fails_does_not_throw_the_answer_away(self):
        work = _Work(POLL * 2)
        assert await ClientConnection(_Caller(None, receive_fails=True)).unless_it_closes(work.run()) == 'answer'
        assert work.finished and not work.cancelled

    async def test_a_caller_who_leaves_cancels_the_work(self):
        work = _Work(60)
        async with asyncio.timeout(2):
            assert await ClientConnection(_Caller(POLL * 2)).unless_it_closes(work.run()) is None
        assert work.cancelled and not work.finished

    async def test_a_failure_still_reaches_the_caller(self):
        async def refuse():
            raise HTTPException(status_code=502, detail='no')

        with pytest.raises(HTTPException) as caught:
            await ClientConnection(_Caller(None)).unless_it_closes(refuse())
        assert caught.value.status_code == 502

    async def test_the_handler_itself_cancelled_cancels_the_work(self):
        work = _Work(60)
        task = asyncio.ensure_future(ClientConnection(_Caller(None)).unless_it_closes(work.run()))
        await asyncio.sleep(POLL * 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert work.cancelled


class TestWhatIsLogged:
    async def test_a_watch_that_fails_is_a_warning_that_says_why(self, caplog):
        caplog.set_level('WARNING', logger='app.api.base_v1.endpoints.client.client_connection')

        await ClientConnection(_Caller(None, receive_fails=True)).unless_it_closes(_Work(POLL * 2).run())

        assert 'Could not watch for a disconnect: receive broke' in caplog.messages

    async def test_a_caller_who_leaves_is_noted(self, caplog):
        caplog.set_level('INFO', logger='app.api.base_v1.endpoints.client.client_connection')

        async with asyncio.timeout(2):
            await ClientConnection(_Caller(POLL)).unless_it_closes(_Work(60).run())

        assert 'Client left before the completion arrived; cancelling the upstream call' in caplog.messages


class TestTheAccessLine:
    """`closed_response`: a 499, and the access line uvicorn would not write."""

    @staticmethod
    def _closed(scope_extra: dict, caplog) -> tuple[int, list[str]]:
        scope = {
            'type': 'http',
            'method': 'POST',
            'path': '/chat/completions',
            'root_path': '',
            'query_string': b'trace=1',
            'headers': [],
            'client': ('10.0.0.7', 51234),
            **scope_extra,
        }
        caplog.set_level('INFO', logger='uvicorn.access')
        response = ClientConnection(Request(scope)).closed_response()
        return response.status_code, [r.getMessage() for r in caplog.records if r.name == 'uvicorn.access']

    def test_it_is_a_499_in_uvicorns_shape(self, caplog):
        status, lines = self._closed({'http_version': '2'}, caplog)

        assert status == 499
        assert lines == ['10.0.0.7:51234 - "POST /chat/completions?trace=1 HTTP/2" 499']

    def test_a_scope_with_no_http_version_says_1_1(self, caplog):
        _, lines = self._closed({}, caplog)

        assert lines == ['10.0.0.7:51234 - "POST /chat/completions?trace=1 HTTP/1.1" 499']


class TestTheUpstreamCall:
    async def test_leaving_closes_the_upstream_request_releases_the_lease_and_bills_the_prompt(self):
        """Through the completion service and a real `httpx` client: the
        node's request is cancelled mid-flight and the lease comes back.
        The prompt's prefill ran, so it is recorded — estimated, as a
        stream without a usage block is — with no output."""
        upstream = {'started': 0, 'cancelled': 0, 'finished': 0}

        async def slow_node(_request: httpx.Request) -> httpx.Response:
            upstream['started'] += 1
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                upstream['cancelled'] += 1
                raise
            upstream['finished'] += 1
            return httpx.Response(200, json={'choices': []})

        pool, usage = SpyPool(), UsageRows()
        prompt = request([{'role': 'user', 'content': 'Summarise the attached contract in three bullet points.'}])
        ctx = context(pool, usage_db=usage, upstream_request=prompt)

        async with asyncio.timeout(5):
            answer = await ClientConnection(_Caller(POLL * 3)).unless_it_closes(
                completions(on_transport(slow_node)).complete(ctx)
            )

        assert answer is None
        assert upstream == {'started': 1, 'cancelled': 1, 'finished': 0}
        assert pool.released == ['lease-1']
        prompt_tokens = TokenEstimator().estimate(messages=prompt.messages, output_pieces=[]).prompt_tokens
        assert prompt_tokens > 0
        assert len(usage.rows) == 1, usage.rows
        row = usage.rows[0]
        assert (row['input_tokens'], row['output_tokens'], row['total_tokens']) == (prompt_tokens, 0, prompt_tokens)
        assert row['stream'] is False
        assert (row['user_id'], row['org_id'], row['model']) == ('user-1', 'org-1', 'fake-model')


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class _PassThrough(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        return await call_next(request)


async def test_under_uvicorn_a_client_timeout_cancels_the_work():
    """The CLI's failure, end to end: a client that times out, behind the
    same kind of middleware stack the app has. Without the race the
    handler sleeps on to the end; with it, it is cancelled within a poll
    or two of the client going."""
    work = _Work(30)
    app = FastAPI()
    app.add_middleware(_PassThrough)
    app.add_middleware(_PassThrough)

    @app.post('/chat/completions')
    async def completions(request: Request, body: dict):
        answer = await ClientConnection(request).unless_it_closes(work.run())
        if answer is None:
            return JSONResponse({}, status_code=499)
        return {'answer': answer}

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning', lifespan='off'))
    serving = asyncio.ensure_future(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started

        async with httpx.AsyncClient(timeout=0.3) as client:
            with pytest.raises(httpx.ReadTimeout):
                await client.post(f'http://127.0.0.1:{port}/chat/completions', json={'messages': []})

        for _ in range(100):
            if work.cancelled:
                break
            await asyncio.sleep(0.01)
        assert work.cancelled and not work.finished
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, 5)


class _NoRoom:
    """A chat pool whose every node is full."""

    def __init__(self) -> None:
        self.asked = 0

    async def acquire(self, user_id, *, model=None):
        self.asked += 1
        raise NoBackendAvailableError()

    async def release(self, lease):
        raise AssertionError('nothing was leased')


async def test_under_uvicorn_a_client_timeout_leaves_the_model_queue(monkeypatch):
    """A request waiting for a free node, behind the app's kind of
    middleware stack, whose client times out. It used to stay queued —
    `FairQueue.take` polled `request.is_disconnected()`, which never saw
    the disconnect behind `BaseHTTPMiddleware` — holding its place for
    the full 15-minute wait; now it leaves within a moment of the client."""
    pool = _NoRoom()
    monkeypatch.setattr(settings, 'chat_queue_max_wait_seconds', 30)

    app = FastAPI()
    app.add_middleware(_PassThrough)
    app.add_middleware(_PassThrough)
    app.include_router(chat_module.router)
    queue = FairQueue(max_length=settings.chat_queue_max_length)
    services = ChatServices(pools=pools(pool), callers=person(), usage_db=UsageRows(), queue=queue)
    app.dependency_overrides[chat_services] = lambda: services

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning', lifespan='off'))
    serving = asyncio.ensure_future(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started

        async with httpx.AsyncClient(timeout=0.5) as client:
            with pytest.raises(httpx.ReadTimeout):
                await client.post(
                    f'http://127.0.0.1:{port}/chat/completions',
                    json={'messages': [{'role': 'user', 'content': 'hi'}]},
                    headers={'Authorization': 'Bearer t'},
                )
        assert pool.asked >= 1

        for _ in range(150):
            if queue.waiting == 0:
                break
            await asyncio.sleep(0.02)
        assert queue.waiting == 0
    finally:
        # Forced: a request still queued would otherwise hold the shutdown.
        server.should_exit = server.force_exit = True
        await asyncio.wait_for(serving, 5)


class _OneNode:
    """A chat pool with one free node."""

    async def acquire(self, user_id, *, model=None, exclude_backend_id=None):
        return lease()

    async def release(self, lease):
        return None


@pytest.mark.parametrize('where', ['waiting for a node', 'waiting for the answer'])
async def test_under_uvicorn_a_client_that_gives_up_leaves_a_499_in_the_access_log(monkeypatch, caplog, where):
    """The 499 is for the access log — the client has gone and never sees
    it — and uvicorn writes no access line for a response to a connection
    that has closed. So the rerun's abandoned request left only "Client
    left…" behind, and a log reader counting outcomes saw nothing. B32."""
    pool = _NoRoom() if where == 'waiting for a node' else _OneNode()
    monkeypatch.setattr(settings, 'chat_queue_max_wait_seconds', 30)
    work = _Work(30)

    class _SlowNode:
        async def complete(self, *, lease, request):
            return await work.run()

    caplog.set_level('INFO', logger='uvicorn.access')

    app = FastAPI()
    app.add_middleware(_PassThrough)
    app.add_middleware(_PassThrough)
    app.include_router(chat_module.router)
    services = ChatServices(pools=pools(pool), callers=person(), upstream=_SlowNode(), usage_db=UsageRows())
    app.dependency_overrides[chat_services] = lambda: services

    port = _free_port()
    # No log_config: uvicorn's own stops `uvicorn.access` propagating to
    # the root logger, where caplog listens.
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_config=None, lifespan='off'))
    serving = asyncio.ensure_future(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started

        async with httpx.AsyncClient(timeout=0.5) as client:
            with pytest.raises(httpx.ReadTimeout):
                await client.post(
                    f'http://127.0.0.1:{port}/chat/completions?trace=1',
                    json={'messages': [{'role': 'user', 'content': 'hi'}]},
                    headers={'Authorization': 'Bearer t'},
                )

        def access_lines():
            return [r.getMessage() for r in caplog.records if r.name == 'uvicorn.access']

        for _ in range(150):
            if any('499' in line for line in access_lines()):
                break
            await asyncio.sleep(0.02)
        lines = [line for line in access_lines() if '/chat/completions' in line]
        assert len(lines) == 1, lines
        assert lines[0].startswith('127.0.0.1:'), lines
        assert lines[0].endswith('"POST /chat/completions?trace=1 HTTP/1.1" 499'), lines
    finally:
        server.should_exit = server.force_exit = True
        await asyncio.wait_for(serving, 5)
