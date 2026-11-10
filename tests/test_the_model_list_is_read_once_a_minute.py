"""`ChatModelCatalog`: the pool's distinct model ids, read once per TTL.

`/chat/models` is called by every client on start; the listing is
cached so that does not reach the pool each time. A failed read is not
cached, and `clear` forgets.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.api.base_v1.schema.chat import ChatModelEntry
from app.services.ai.chat.catalog import ChatModelCatalog


class _Pool:
    def __init__(self, *answers: object) -> None:
        self._answers = list(answers)
        self.reads = 0

    async def list_available_models(self) -> list[tuple[str, str]]:
        answer = self._answers[min(self.reads, len(self._answers) - 1)]
        self.reads += 1
        if isinstance(answer, Exception):
            raise answer
        return answer  # type: ignore[return-value]


def _catalog(pool: _Pool, *, ttl: float = 60) -> ChatModelCatalog:
    return ChatModelCatalog(pools=SimpleNamespace(get_pool=lambda: pool), ttl=ttl)  # type: ignore[arg-type]


_TWO = [('m-a', 'http://n1/v1'), ('m-b', 'http://n1/v1'), ('m-a', 'http://n2/v1')]


async def test_each_model_is_listed_once_in_the_order_first_named():
    assert await _catalog(_Pool(_TWO)).models() == [ChatModelEntry(id='m-a'), ChatModelEntry(id='m-b')]


async def test_a_second_call_within_the_ttl_does_not_reach_the_pool():
    pool = _Pool(_TWO, [('m-c', 'http://n3/v1')])
    catalog = _catalog(pool)

    await catalog.models()
    again = await catalog.models()

    assert pool.reads == 1
    assert [m.id for m in again] == ['m-a', 'm-b']


async def test_after_the_ttl_the_pool_is_read_again():
    pool = _Pool(_TWO, [('m-c', 'http://n3/v1')])
    catalog = _catalog(pool, ttl=0.05)

    await catalog.models()
    await asyncio.sleep(0.1)

    assert [m.id for m in await catalog.models()] == ['m-c']
    assert pool.reads == 2


async def test_clear_forgets_the_listing():
    pool = _Pool(_TWO, [('m-c', 'http://n3/v1')])
    catalog = _catalog(pool)
    await catalog.models()

    catalog.clear()

    assert [m.id for m in await catalog.models()] == ['m-c']


async def test_a_failed_read_is_not_cached():
    pool = _Pool(ConnectionError('redis down'), _TWO)
    catalog = _catalog(pool)

    with pytest.raises(ConnectionError):
        await catalog.models()

    assert [m.id for m in await catalog.models()] == ['m-a', 'm-b']


async def test_a_caller_that_changes_its_list_does_not_change_the_cache():
    catalog = _catalog(_Pool(_TWO))

    (await catalog.models()).clear()

    assert len(await catalog.models()) == 2
