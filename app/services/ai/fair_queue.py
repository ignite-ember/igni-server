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
import contextlib
import time

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

#: Gives a lease back: `pool.release(lease)`.
Release = Callable[[BackendLease], Awaitable[None]]


class Waiter:
    """One queued request: who it is for, how to lease for it, and the lease or refusal it is given."""

    def __init__(self, *, org: str, producer: str, user_id: str, model: str | None, acquire: Acquire) -> None:
        self.org = org
        self.producer = producer
        self._user_id = user_id
        self._model = model
        self._acquire = acquire
        self._outcome: asyncio.Future[BackendLease] = asyncio.get_running_loop().create_future()
        self.queued_at = time.monotonic()

    @property
    def settled(self) -> bool:
        """Given its lease or refusal, or abandoned."""
        return self._outcome.done()

    async def try_lease(self) -> BackendLease | None:
        """A lease for this waiter if a node has room now; None when every node is full."""
        try:
            return await self._acquire(self._user_id, self._model)
        except NoBackendAvailableError:
            return None

    def grant(self, lease: BackendLease) -> None:
        if not self._outcome.done():
            self._outcome.set_result(lease)

    def refuse(self, error: Exception) -> None:
        if not self._outcome.done():
            self._outcome.set_exception(error)

    async def wait(self, *, max_wait: float) -> BackendLease:
        """The lease once granted; QueueTimeout *max_wait* after it joined."""
        remaining = self.queued_at + max_wait - time.monotonic()
        try:
            return await asyncio.wait_for(asyncio.shield(self._outcome), max(remaining, 0))
        except TimeoutError:
            raise QueueTimeout() from None

    async def abandon(self, release: Release) -> None:
        """It gave up: a lease granted just as it did goes back through *release*; otherwise none comes."""
        if not self._outcome.done():
            self._outcome.cancel()
        elif not self._outcome.cancelled() and self._outcome.exception() is None:
            await release(self._outcome.result())


class FairQueue:
    """The replica's waiting model requests; one per app, owned by its composition root."""

    def __init__(self, *, max_length: int) -> None:
        self._max_length = max_length
        #: org → producer → its waiters, oldest first. Insertion order is the turn order.
        self._orgs: OrderedDict[str, OrderedDict[str, deque[Waiter]]] = OrderedDict()
        self._length = 0
        self._wake: asyncio.Event | None = None
        self._dispatcher: asyncio.Task[None] | None = None

    @property
    def waiting(self) -> int:
        return self._length

    def depth(self, org: str) -> int:
        return sum(len(waiters) for waiters in self._orgs.get(org, {}).values())

    def kick(self) -> None:
        """A slot may have come free."""
        if self._wake is not None:
            self._wake.set()

    async def take(
        self,
        *,
        org: str,
        producer: str,
        user_id: str,
        model: str | None,
        acquire: Acquire,
        release: Release,
        max_wait: float,
    ) -> BackendLease:
        """A lease now if there is room and nobody waits; otherwise one in turn.

        Raises QueueFull at once past the queue's length and QueueTimeout
        after *max_wait*. A caller who leaves cancels the take (the chat
        route races it against the connection's disconnect); it leaves
        the queue, and a slot granted just as it gave up goes back
        through *release*.
        """
        waiter = Waiter(org=org, producer=producer, user_id=user_id, model=model, acquire=acquire)
        if self._length == 0:
            lease = await waiter.try_lease()
            if lease is not None:
                return lease
        if self._length >= self._max_length:
            raise QueueFull()
        self._join(waiter)
        try:
            return await waiter.wait(max_wait=max_wait)
        except BaseException:
            self._leave(waiter)
            await waiter.abandon(release)
            raise
        finally:
            CHAT_QUEUE_WAIT.labels(org=org).observe(time.monotonic() - waiter.queued_at)

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
        """A dispatcher running on this loop; its wake-up event is its own, so it never outlives its loop."""
        if self._dispatcher is None or self._dispatcher.done():
            self._wake = asyncio.Event()
            self._dispatcher = asyncio.get_running_loop().create_task(self._run(self._wake))

    async def _run(self, wake: asyncio.Event) -> None:
        while self._length:
            await self.dispatch()
            wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), TICK_S)

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
            if not await self._serve_one_turn():
                return granted
            granted += 1
        return granted

    async def _serve_one_turn(self) -> bool:
        """Grant the first waiter in rotation a node has room for; False when none could be served."""
        for waiter in self._rotation():
            if waiter.settled:
                self._leave(waiter)
                continue
            try:
                lease = await waiter.try_lease()
            except ModelNotAvailableError as exc:
                self._served(waiter)
                waiter.refuse(exc)
                continue
            if lease is None:
                continue
            self._served(waiter)
            waiter.grant(lease)
            # One turn each: the next call starts the rotation again from the next in line.
            return True
        return False

    def _served(self, waiter: Waiter) -> None:
        """Out of the queue; its org and producer go to the back of their lines."""
        self._leave(waiter)
        producers = self._orgs.get(waiter.org)
        if producers is None:
            return
        if waiter.producer in producers:
            producers.move_to_end(waiter.producer)
        self._orgs.move_to_end(waiter.org)
