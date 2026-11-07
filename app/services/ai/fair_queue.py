"""When every model node is full, requests wait their turn — fairly between organisations, then producers.

A request that finds a free slot takes it at once, as before, unless
others are already waiting (it does not jump them). Otherwise it joins
the queue of its **organisation**, under its **producer** — a person,
an app, each space — and waits. Whenever a slot may have come free (a
lease released in this replica, and every quarter second for those
released elsewhere) the dispatcher hands out slots taking **turns
between organisations**, and within one, **turns between its
producers**, oldest request first for each. So an organisation with a
hundred busy producers gets one turn in each round, like one with a
single request, and none of its producers can crowd out the others.

The per-node in-flight cap is the pool's, untouched: the dispatcher
only asks the pool for a lease, which still prefers the node holding the
caller's KV cache when it has room. A request waits at most
`CHAT_QUEUE_MAX_WAIT_SECONDS` (default 15 minutes) and the queue holds at
most `CHAT_QUEUE_MAX_LENGTH`; past either, 429. A client that goes away
leaves the queue. The queue is each API replica's own; nodes' capacity
is shared through Redis, so replicas take slots as they come free, each
serving its own waiters fairly (`docs/service-identity.md` §19).
"""

from __future__ import annotations

import asyncio
from collections import deque, OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import time
from typing import ClassVar
import weakref

from app.monitoring import CHAT_QUEUE_DEPTH, CHAT_QUEUE_WAIT
from app.services.ai.schemas import BackendLease, ModelNotAvailableError, NoBackendAvailableError

#: How often the dispatcher looks again while anyone waits, for slots freed by another replica.
TICK_S = 0.25


class QueueFull(Exception):
    """The queue is at its length; refused at once."""


class QueueTimeout(Exception):
    """Waited the longest a request may."""


#: Takes a lease for one waiter: `pool.acquire(user_id, model=...)`.
Acquire = Callable[[str, str | None], Awaitable[BackendLease]]


@dataclass(eq=False)
class Waiter:
    org: str
    producer: str
    user_id: str
    model: str | None
    acquire: Acquire
    future: asyncio.Future[BackendLease]
    queued_at: float = field(default_factory=time.monotonic)


class FairQueue:
    """One per event loop: the replica's waiting model requests."""

    _queues: ClassVar[weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, FairQueue]] = weakref.WeakKeyDictionary()

    def __init__(self, *, max_length: int) -> None:
        self._max_length = max_length
        #: org → producer → its waiters, oldest first. Insertion order is the turn order.
        self._orgs: OrderedDict[str, OrderedDict[str, deque[Waiter]]] = OrderedDict()
        self._length = 0
        self._wake = asyncio.Event()
        self._dispatcher: asyncio.Task[None] | None = None

    @classmethod
    def of_loop(cls, *, max_length: int) -> FairQueue:
        loop = asyncio.get_running_loop()
        queue = cls._queues.get(loop)
        if queue is None:
            queue = cls._queues[loop] = cls(max_length=max_length)
        queue._max_length = max_length
        return queue

    @property
    def waiting(self) -> int:
        return self._length

    def depth(self, org: str) -> int:
        return sum(len(waiters) for waiters in self._orgs.get(org, {}).values())

    def kick(self) -> None:
        """A slot may have come free."""
        self._wake.set()

    async def take(
        self,
        *,
        org: str,
        producer: str,
        user_id: str,
        model: str | None,
        acquire: Acquire,
        release: Callable[[BackendLease], Awaitable[None]],
        max_wait: float,
    ) -> BackendLease:
        """A lease now if there is room and nobody waits; otherwise one in turn.

        Raises QueueFull at once past the queue's length and QueueTimeout
        after *max_wait*. A caller who leaves cancels the take (the chat
        route races it against the connection's disconnect); it leaves
        the queue, and a slot granted just as it gave up goes back
        through *release*.
        """
        if self._length == 0:
            try:
                return await acquire(user_id, model)
            except NoBackendAvailableError:
                pass
        if self._length >= self._max_length:
            raise QueueFull()
        waiter = Waiter(
            org=org,
            producer=producer,
            user_id=user_id,
            model=model,
            acquire=acquire,
            future=asyncio.get_running_loop().create_future(),
        )
        self._join(waiter)
        try:
            return await self._await(waiter, max_wait=max_wait)
        except BaseException:
            self._leave(waiter)
            if waiter.future.done() and not waiter.future.cancelled() and waiter.future.exception() is None:
                await release(waiter.future.result())
            elif not waiter.future.done():
                waiter.future.cancel()
            raise
        finally:
            CHAT_QUEUE_WAIT.labels(org=org).observe(time.monotonic() - waiter.queued_at)

    @staticmethod
    async def _await(waiter: Waiter, *, max_wait: float) -> BackendLease:
        remaining = waiter.queued_at + max_wait - time.monotonic()
        try:
            return await asyncio.wait_for(asyncio.shield(waiter.future), max(remaining, 0))
        except TimeoutError:
            raise QueueTimeout() from None

    def _join(self, waiter: Waiter) -> None:
        producers = self._orgs.setdefault(waiter.org, OrderedDict())
        producers.setdefault(waiter.producer, deque()).append(waiter)
        self._length += 1
        CHAT_QUEUE_DEPTH.labels(org=waiter.org).set(self.depth(waiter.org))
        self._ensure_dispatcher()
        self.kick()

    def _leave(self, waiter: Waiter) -> bool:
        """Take *waiter* out; False when it was no longer queued (it was granted or failed)."""
        producers = self._orgs.get(waiter.org)
        queue = producers.get(waiter.producer) if producers else None
        if queue is None or waiter not in queue:
            return False
        queue.remove(waiter)
        self._length -= 1
        self._tidy(waiter.org, waiter.producer)
        return True

    def _tidy(self, org: str, producer: str) -> None:
        producers = self._orgs.get(org)
        if producers is not None and not producers.get(producer):
            producers.pop(producer, None)
        if producers is not None and not producers:
            self._orgs.pop(org, None)
        CHAT_QUEUE_DEPTH.labels(org=org).set(self.depth(org))

    def _ensure_dispatcher(self) -> None:
        if self._dispatcher is None or self._dispatcher.done():
            self._dispatcher = asyncio.get_running_loop().create_task(self._run())

    async def _run(self) -> None:
        while self._length:
            await self.dispatch()
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), TICK_S)
            except TimeoutError:
                pass

    def _rotation(self) -> list[Waiter]:
        """The next waiter of every producer, in turn order: one org's first producer, the next org's, …"""
        order: list[Waiter] = []
        lists = [list(producers.items()) for producers in self._orgs.values()]
        for depth in range(max((len(items) for items in lists), default=0)):
            for items in lists:
                if depth < len(items):
                    order.append(items[depth][1][0])
        return order

    async def dispatch(self) -> int:
        """Hand out slots in turn until none is free (or nobody waits). Returns how many."""
        granted = 0
        while self._length:
            progressed = False
            for waiter in self._rotation():
                if waiter.future.done():
                    self._leave(waiter)
                    continue
                try:
                    lease = await waiter.acquire(waiter.user_id, waiter.model)
                except NoBackendAvailableError:
                    continue
                except ModelNotAvailableError as exc:
                    self._grant(waiter, exc)
                    continue
                self._grant(waiter, lease)
                granted += 1
                progressed = True
                # One turn each: start the rotation again from the next in line.
                break
            if not progressed:
                return granted
        return granted

    def _grant(self, waiter: Waiter, outcome: BackendLease | Exception) -> None:
        self._leave(waiter)
        self._take_turn(waiter)
        if not waiter.future.done():
            if isinstance(outcome, Exception):
                waiter.future.set_exception(outcome)
            else:
                waiter.future.set_result(outcome)

    def _take_turn(self, waiter: Waiter) -> None:
        """The served org and producer go to the back of their lines."""
        producers = self._orgs.get(waiter.org)
        if producers is None:
            return
        if waiter.producer in producers:
            producers.move_to_end(waiter.producer)
        self._orgs.move_to_end(waiter.org)
