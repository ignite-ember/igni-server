"""`ChatAdmission`: a node's lease now, one in turn, or a refusal that says why.

Driven through a real `FairQueue` over a fake pool, so the arguments
the queue is given and the refusal each failure becomes are what a
request meets.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.services.ai.backend_provider import BackendConfigError
from app.services.ai.chat.admission import ChatAdmission
from app.services.ai.chat.errors import (
    BackendMisconfiguredRefusal,
    ModelUnavailableRefusal,
    QueueFullRefusal,
    QueueTimeoutRefusal,
)
from app.services.ai.chat.schemas import ChatCaller
from app.services.ai.fair_queue import FairQueue
from app.services.ai.schemas import ModelNotAvailableError, NoBackendAvailableError
from tests.chat_proxy import lease


@dataclass
class _Pool:
    """Answers each acquire with *outcome*: a lease, or an exception to raise."""

    outcome: object
    asked: list[tuple[str, str | None]] = field(default_factory=list)

    async def acquire(self, user_id: str, *, model: str | None = None):
        self.asked.append((user_id, model))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    async def release(self, _lease) -> None:
        return None


@dataclass
class _SpyQueue:
    """Records what `take` was given, and hands back a lease."""

    taken: dict = field(default_factory=dict)

    async def take(self, **kwargs):
        self.taken = kwargs
        return lease(1)


ADA = ChatCaller(user_id='ada', org_id='org-1')


def _admission(pool, *, queue=None, max_wait: float = 0.05) -> ChatAdmission:
    return ChatAdmission(pool=pool, queue=queue or FairQueue(max_length=10), max_wait=max_wait)  # type: ignore[arg-type]


async def test_a_free_node_is_leased_for_the_model_asked_for():
    pool = _Pool(lease(7))

    granted = await _admission(pool).admit(caller=ADA, model='m-1')

    assert granted.lease_id == 'lease-7'
    assert pool.asked == [('ada', 'm-1')]


async def test_the_queue_is_given_the_callers_org_and_the_caller_as_producer():
    pool = _Pool(lease(1))
    queue = _SpyQueue()

    await _admission(pool, queue=queue, max_wait=12.5).admit(caller=ADA, model=None)

    assert {k: v for k, v in queue.taken.items() if k not in {'acquire', 'release'}} == {
        'org': 'org-1',
        'producer': 'ada',
        'user_id': 'ada',
        'model': None,
        'max_wait': 12.5,
    }
    assert queue.taken['release'] == pool.release


async def test_a_person_in_no_organisation_queues_under_the_empty_org():
    queue = _SpyQueue()

    await _admission(_Pool(lease(1)), queue=queue).admit(caller=ChatCaller(user_id='bo', org_id=None), model=None)

    assert queue.taken['org'] == ''


async def test_a_model_nobody_serves_is_a_404_in_the_pools_words():
    error = ModelNotAvailableError(model='gpt-9', available=['m-1'])

    with pytest.raises(ModelUnavailableRefusal) as caught:
        await _admission(_Pool(error)).admit(caller=ADA, model='gpt-9')

    assert (caught.value.status_code, caught.value.detail, caught.value.retry_after) == (404, str(error), None)


async def test_a_misconfigured_pool_is_a_503_in_its_own_words():
    with pytest.raises(BackendMisconfiguredRefusal) as caught:
        await _admission(_Pool(BackendConfigError('No inference backends are available.'))).admit(
            caller=ADA, model=None
        )

    assert (caught.value.status_code, caught.value.detail, caught.value.retry_after) == (
        503,
        'No inference backends are available.',
        None,
    )


async def test_a_full_queue_is_refused_at_once():
    with pytest.raises(QueueFullRefusal) as caught:
        await _admission(_Pool(NoBackendAvailableError()), queue=FairQueue(max_length=0)).admit(caller=ADA, model=None)

    assert (caught.value.status_code, caught.value.retry_after) == (429, 30)


async def test_a_wait_that_runs_out_is_refused_in_so_many_words():
    with pytest.raises(QueueTimeoutRefusal) as caught:
        await _admission(_Pool(NoBackendAvailableError()), max_wait=0.05).admit(caller=ADA, model=None)

    assert caught.value.status_code == 429
    assert caught.value.detail == 'Waited the longest a request may for a free inference node. Please retry.'
    assert caught.value.retry_after == 30
