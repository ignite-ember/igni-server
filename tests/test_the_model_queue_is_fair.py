"""When every model node is full, requests wait their turn: between organisations first, then producers.

An organisation with many busy producers cannot starve another's single
request, and within an organisation no producer crowds out the others
(`docs/service-identity.md` §19). Driven against a pool with one slot, so
every grant is one turn.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.ai.fair_queue import FairQueue, QueueFull, QueueTimeout
from app.services.ai.schemas import BackendLease, NoBackendAvailableError

pytestmark = pytest.mark.anyio


class OneSlot:
    """A model pool with one concurrent slot, held by the test until it frees it."""

    def __init__(self, queue: FairQueue) -> None:
        self.queue = queue
        self.in_use = 1
        self.served = 0

    async def acquire(self, user_id: str, model: str | None) -> BackendLease:
        if self.in_use >= 1:
            raise NoBackendAvailableError('chat')
        self.in_use += 1
        self.served += 1
        return BackendLease(
            lease_id=f'l{self.served}', backend_id='gpu', api_key=None, base_url='http://gpu/v1', model='m'
        )

    async def release(self, lease: BackendLease) -> None:
        self.in_use -= 1
        self.queue.kick()

    async def free_one_and_wait(self, grants: list) -> None:
        before = len(grants)
        self.in_use -= 1
        self.queue.kick()
        for _ in range(200):
            if len(grants) > before:
                return
            await asyncio.sleep(0.005)
        raise AssertionError('nothing was granted')


async def _waiting(queue: FairQueue, count: int) -> None:
    for _ in range(200):
        if queue.waiting == count:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f'{queue.waiting} waiting, not {count}')


def _ask(queue: FairQueue, pool: OneSlot, grants: list, org: str, producer: str, **extra) -> asyncio.Task:
    async def run() -> None:
        await queue.take(
            org=org,
            producer=producer,
            user_id=producer,
            model=None,
            acquire=pool.acquire,
            release=pool.release,
            max_wait=extra.get('max_wait', 60),
            gone=extra.get('gone'),
        )
        grants.append((org, producer))

    return asyncio.create_task(run())


async def test_a_busy_organisation_cannot_starve_another():
    queue = FairQueue(max_length=100)
    pool = OneSlot(queue)
    grants: list[tuple[str, str]] = []
    tasks = [_ask(queue, pool, grants, 'big', f'p{n}') for n in range(5) for _ in range(3)]
    await _waiting(queue, 15)
    tasks.append(_ask(queue, pool, grants, 'small', 'alone'))
    await _waiting(queue, 16)

    await pool.free_one_and_wait(grants)
    await pool.free_one_and_wait(grants)

    assert ('small', 'alone') in grants[:2]
    for task in tasks:
        task.cancel()


async def test_within_an_organisation_producers_take_turns():
    queue = FairQueue(max_length=100)
    pool = OneSlot(queue)
    grants: list[tuple[str, str]] = []
    tasks = [_ask(queue, pool, grants, 'acme', producer) for producer in ('ada', 'ada', 'ada', 'max', 'max', 'eve')]
    await _waiting(queue, 6)

    for _ in range(6):
        await pool.free_one_and_wait(grants)

    assert [producer for _, producer in grants] == ['ada', 'max', 'eve', 'ada', 'max', 'ada']
    await asyncio.gather(*tasks)


async def test_with_room_and_nobody_waiting_it_is_at_once():
    queue = FairQueue(max_length=100)
    pool = OneSlot(queue)
    pool.in_use = 0

    lease = await queue.take(
        org='acme', producer='ada', user_id='ada', model=None, acquire=pool.acquire, release=pool.release, max_wait=1
    )

    assert lease.lease_id == 'l1'
    assert queue.waiting == 0


async def test_a_client_that_goes_away_leaves_the_queue():
    queue = FairQueue(max_length=100)
    pool = OneSlot(queue)
    left = asyncio.Event()

    async def gone() -> bool:
        return left.is_set()

    task = _ask(queue, pool, [], 'acme', 'ada', gone=gone)
    await _waiting(queue, 1)
    left.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert queue.waiting == 0


async def test_the_wait_is_bounded_and_so_is_the_queue():
    queue = FairQueue(max_length=1)
    pool = OneSlot(queue)
    first = _ask(queue, pool, [], 'acme', 'ada', max_wait=0.3)
    await _waiting(queue, 1)

    with pytest.raises(QueueFull):
        await queue.take(
            org='acme',
            producer='max',
            user_id='max',
            model=None,
            acquire=pool.acquire,
            release=pool.release,
            max_wait=1,
        )
    with pytest.raises(QueueTimeout):
        await first
    assert queue.waiting == 0
