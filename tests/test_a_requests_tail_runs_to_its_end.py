"""A request's tail — its usage row and its node's release — finishes even when the request is cancelled.

Starlette cancels a response whose client left, and the cancellation is
delivered again at every await inside its scope. The tail runs in its
own task behind a shield, so the wait for it is cancelled and the work
is not: the row is written and the slot comes back.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.ai.chat.tails import TailRunner


async def test_cancelling_the_caller_stops_the_wait_and_not_the_work():
    started, gate, done = asyncio.Event(), asyncio.Event(), []

    async def tail() -> None:
        started.set()
        await gate.wait()
        done.append('written')

    waiting = asyncio.create_task(TailRunner().run(tail()))
    await started.wait()
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    gate.set()
    for _ in range(3):
        await asyncio.sleep(0)

    assert done == ['written']


async def test_an_uncancelled_caller_waits_for_the_tail_and_sees_its_failure():
    runner = TailRunner()
    done = []

    async def tail() -> None:
        await asyncio.sleep(0)
        done.append('written')

    async def failing() -> None:
        raise RuntimeError('the usage table is gone')

    await runner.run(tail())
    with pytest.raises(RuntimeError, match='the usage table is gone'):
        await runner.run(failing())

    assert done == ['written']
