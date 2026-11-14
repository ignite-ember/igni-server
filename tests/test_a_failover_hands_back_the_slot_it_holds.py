"""A non-streamed completion that moves to another node returns the slot it actually holds.

The failover used to take the new node's lease, sleep the backoff, and
only then point the request at it. A caller who left during that sleep
cancelled the work; the `finally` released the old lease a second time,
and the new one sat on its node until the pool's 900s lease timeout.
The request now moves to the new lease before it pauses.

Alongside: what the request's context does when its own collaborators
fail, and which attempts are billed when a node answers with something
that is not a completion.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from pydantic import PrivateAttr
import pytest

from app.api.base_v1.schema.chat import CompletionUsage
from app.services.ai.chat.errors import NoOtherNode, UpstreamRefusal
from app.services.ai.chat.request_context import ChatRequestContext
from app.services.ai.upstream import RetryPolicy
from tests.chat_proxy import completions, context, lease, on_transport, request, SpyPool, UsageRows


class _PausesUntilCancelled(RetryPolicy):
    """A backoff that says when it starts and then never ends."""

    _paused: asyncio.Event = PrivateAttr(default_factory=asyncio.Event)

    async def pause(self, attempt: int) -> None:
        self._paused.set()
        await asyncio.Event().wait()


def _first_refuses_then(reply: httpx.Response):
    calls = {'n': 0}

    def node(_request: httpx.Request) -> httpx.Response:
        calls['n'] += 1
        if calls['n'] == 1:
            raise httpx.ConnectError('All connection attempts failed')
        return reply

    return on_transport(node)


def _replies(*bodies: object):
    """A node that answers 200 with each body in turn, repeating the last."""
    calls = {'n': 0}

    def node(_request: httpx.Request) -> httpx.Response:
        body = bodies[min(calls['n'], len(bodies) - 1)]
        calls['n'] += 1
        return httpx.Response(200, text=body if isinstance(body, str) else json.dumps(body))

    return on_transport(node)


_GOOD = {
    'id': 'done',
    'created': 7,
    'model': 'fake-model',
    'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'hello'}, 'finish_reason': 'stop'}],
    'usage': {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5},
}


class TestACallerWhoLeavesDuringTheBackoff:
    async def test_the_new_nodes_slot_comes_back_not_the_old_one_twice(self):
        pool = SpyPool()
        retry = _PausesUntilCancelled(backoff=0)
        task = asyncio.ensure_future(
            completions(_first_refuses_then(httpx.Response(200, json=_GOOD)), retry=retry).complete(context(pool))
        )
        async with asyncio.timeout(2):
            await retry._paused.wait()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert pool.acquired == ['lease-1', 'lease-2']
        assert pool.released == ['lease-1', 'lease-2'], (
            f'released {pool.released}: the lease taken for the retry would sit on its node for 900s'
        )

    async def test_the_retry_after_the_pause_is_sent_to_the_new_node(self):
        """The switch before the pause is the one the next attempt uses."""
        pool = SpyPool()
        models: list[str] = []
        calls = {'n': 0}

        def node(req: httpx.Request) -> httpx.Response:
            calls['n'] += 1
            models.append(json.loads(req.content)['model'])
            if calls['n'] == 1:
                raise httpx.ConnectError('refused')
            return httpx.Response(200, json=_GOOD)

        pool.model = 'second-model'
        ctx = context(pool, upstream_request=request(model='first-model'))

        await completions(on_transport(node)).complete(ctx)

        assert models == ['first-model', 'second-model']
        assert ctx.lease.lease_id == 'lease-2'


class TestWhatIsBilledForAnAnswerThatIsNotACompletion:
    """A 200 that does not fit the completion shape is unreadable: retried,
    and never billed. It used to write a usage row and then fail building
    the answer, as a bare 500."""

    @pytest.mark.parametrize(
        'broken',
        [
            {'id': 'half', 'choices': [{'index': 0, 'finish_reason': 'stop'}]},
            ['not', 'an', 'object'],
            {'id': 'x', 'choices': [], 'usage': {'prompt_tokens': None, 'total_tokens': 'many'}},
            {'id': 'x', 'created': None, 'choices': []},
        ],
        ids=['choice-without-message', 'not-an-object', 'usage-not-counts', 'null-created'],
    )
    async def test_the_bad_attempt_writes_no_row_and_the_good_one_writes_one(self, broken):
        rows = UsageRows()
        pool = SpyPool()

        answer = await completions(_replies(broken, _GOOD)).complete(context(pool, usage_db=rows))

        assert answer.id == 'done'
        assert pool.acquired == ['lease-1', 'lease-2']
        assert [(r['input_tokens'], r['output_tokens'], r['total_tokens']) for r in rows.rows] == [(3, 2, 5)]

    async def test_every_node_unreadable_is_a_502_with_nothing_billed(self):
        rows = UsageRows()
        pool = SpyPool()

        with pytest.raises(UpstreamRefusal) as caught:
            await completions(_replies({'choices': [{'index': 0}]})).complete(context(pool, usage_db=rows))

        assert caught.value.status_code == 502
        assert caught.value.detail == 'The inference node returned an unreadable response.'
        assert rows.rows == []
        assert sorted(pool.released) == sorted(pool.acquired)


class TestASuccessfulCompletion:
    async def test_choices_with_a_null_index_take_their_position(self):
        message = {'role': 'assistant', 'content': 'x'}
        body = {'id': 'i', 'choices': [{'index': None, 'message': message}, {'index': None, 'message': message}]}

        answer = await completions(_replies(body)).complete(context(SpyPool()))

        assert [choice.index for choice in answer.choices] == [0, 1]

    async def test_a_node_that_reports_no_usage_is_billed_a_row_of_zeros(self):
        rows = UsageRows()
        body = {'id': 'i', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'x'}}]}

        answer = await completions(_replies(body)).complete(context(SpyPool(), usage_db=rows))

        assert answer.usage is None
        assert len(rows.rows) == 1
        row = rows.rows[0]
        assert (row['input_tokens'], row['output_tokens'], row['total_tokens']) == (0, 0, 0)
        assert row['stream'] is False
        assert (row['user_id'], row['org_id'], row['model']) == ('user-1', 'org-1', 'fake-model')

    async def test_a_node_at_capacity_is_answered_429_with_retry_after(self):
        with pytest.raises(UpstreamRefusal) as caught:
            await completions(on_transport(lambda _r: httpx.Response(429, text='busy'))).complete(context(SpyPool()))

        assert caught.value.status_code == 429
        assert caught.value.retry_after == 30


class _FailingPool(SpyPool):
    def __init__(self, *, release_fails: bool = False, acquire_fails: bool = False) -> None:
        super().__init__()
        self._release_fails = release_fails
        self._acquire_fails = acquire_fails

    async def release(self, held):
        if self._release_fails:
            raise ConnectionError('redis went away')
        await super().release(held)

    async def acquire(self, user_id, *, model=None, exclude_backend_id=None):
        if self._acquire_fails:
            raise RuntimeError('pool unreadable')
        return await super().acquire(user_id, model=model, exclude_backend_id=exclude_backend_id)


def _ctx(pool, *, usage_db=None, queue=None) -> ChatRequestContext:
    return ChatRequestContext(
        caller=SimpleNamespace(user_id='user-1', org_id='org-1', app_id=None),  # type: ignore[arg-type]
        request=request(),
        lease=lease(1),
        pool=pool,
        usage_db=usage_db if usage_db is not None else UsageRows(),
        queue=queue or SimpleNamespace(kick=Mock()),  # type: ignore[arg-type]
    )


class TestTheContextWhenItsPartsFail:
    async def test_a_release_that_fails_is_logged_and_the_queue_is_still_kicked(self, caplog):
        queue = SimpleNamespace(kick=Mock())

        await _ctx(_FailingPool(release_fails=True), queue=queue).release()

        queue.kick.assert_called_once_with()
        assert 'Failed to release inference backend: redis went away' in caplog.text

    async def test_a_release_kicks_the_queue_so_the_next_in_turn_takes_the_slot(self):
        queue = SimpleNamespace(kick=Mock())
        pool = SpyPool()

        await _ctx(pool, queue=queue).release()

        assert pool.released == ['lease-1']
        queue.kick.assert_called_once_with()

    async def test_a_failover_whose_release_fails_is_no_other_node_and_takes_none(self):
        pool = _FailingPool(release_fails=True)
        ctx = _ctx(pool)

        with pytest.raises(NoOtherNode, match='redis went away'):
            await ctx.fail_over(failed_backend_id='node-1')

        assert pool.acquired == ['lease-1']
        assert ctx.lease.lease_id == 'lease-1'

    async def test_a_failover_whose_acquire_fails_is_no_other_node(self):
        pool = _FailingPool(acquire_fails=True)
        ctx = _ctx(pool)

        with pytest.raises(NoOtherNode, match='pool unreadable'):
            await ctx.fail_over(failed_backend_id='node-1')

        assert pool.released == ['lease-1']

    async def test_a_usage_write_that_fails_is_a_warning_not_a_failure(self, caplog):
        class _Broken:
            async def log(self, **_row):
                raise RuntimeError('db down')

        caplog.set_level(logging.WARNING)

        await _ctx(SpyPool(), usage_db=_Broken()).log_usage(CompletionUsage(), duration=0.1, stream=False)

        assert 'Failed to log chat usage: db down' in caplog.text

    async def test_the_row_rounds_the_duration_and_names_the_model_sent(self):
        rows = UsageRows()

        await _ctx(SpyPool(), usage_db=rows).log_usage(
            CompletionUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3), duration=1.23456, stream=True
        )

        assert rows.rows == [
            {
                'user_id': 'user-1',
                'org_id': 'org-1',
                'app_id': None,
                'model': 'fake-model',
                'input_tokens': 1,
                'output_tokens': 2,
                'total_tokens': 3,
                'duration': 1.235,
                'stream': True,
                'backend_id': 'node-1',
                'kind': 'openai',
            }
        ]
