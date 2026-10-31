"""One node, many model ids: a base model plus its LoRA adapters.

A vLLM process started with ``--enable-lora --lora-modules sql=... docs=...``
serves its base model *and* every adapter as separate ids on the same
GPU, and a request picks between them with the ordinary ``model``
parameter. They share weights, memory and the KV cache.

Modelling that as one string per node forces a bad choice: either one
entry per adapter — three rows for one GPU, each with its own
concurrency gauge counting the same sequences — or adapters that can
never be requested at all. So a backend holds a *list*, capacity and
stickiness stay per node, and only selection is per model.

The lease test is the one that would bite hardest if it regressed:
sending the base model upstream when an adapter was asked for produces
perfectly plausible output from the wrong weights, with nothing failing.
"""

import json

import pytest

from app.services.ai.backend_pool import InferenceBackendPool
from app.services.ai.backend_provider import InferenceBackendProvider
from app.services.ai.schemas import InferenceBackend, ModelNotAvailableError
from tests.test_inference_backend_pool import FakeRedis, FakeRegistry


def _lora_node(node_id='gpu-01', base='base-27b', adapters=('sql-expert', 'docs-writer')) -> InferenceBackend:
    return InferenceBackend(
        id=node_id,
        base_url=f'http://{node_id}.internal:8000/v1',
        models=[base, *adapters],
        max_concurrent=8,
        pools=['chat'],
    )


@pytest.fixture
def pool(tmp_path):
    def build(backends: list[InferenceBackend]):
        path = tmp_path / f'backends-{id(backends)}.json'
        path.write_text(json.dumps([b.model_dump() for b in backends]))
        instance = InferenceBackendPool(
            'chat', provider=InferenceBackendProvider(path=str(path), registry=FakeRegistry())
        )
        redis = FakeRedis()

        async def _stub():
            return redis

        instance._get_redis = _stub  # type: ignore[method-assign]
        return instance, redis

    return build


class TestConfiguring:
    def test_a_single_model_may_be_written_as_a_string(self):
        """Most nodes serve exactly one model, and making every config say
        ``models: ["x"]`` to express that would be noise."""
        backend = InferenceBackend.model_validate(
            {'id': 'gpu-01', 'base_url': 'http://gpu-01:8000/v1', 'model': 'igni-1.0-27b'}
        )
        assert backend.models == ['igni-1.0-27b']

    def test_a_list_is_the_general_form(self):
        backend = _lora_node()
        assert backend.models == ['base-27b', 'sql-expert', 'docs-writer']

    def test_the_first_entry_is_what_an_unspecified_request_gets(self):
        """Adapters are opt-in by name; the base model is the default."""
        assert _lora_node().primary_model == 'base-27b'

    def test_a_node_serving_nothing_is_refused(self):
        with pytest.raises(ValueError):
            InferenceBackend(id='gpu-01', base_url='http://gpu-01:8000/v1', models=[])


class TestSelection:
    async def test_an_adapter_selects_its_node(self, pool):
        instance, _ = pool([_lora_node('gpu-01', adapters=('sql-expert',)), _lora_node('gpu-02', adapters=())])
        lease = await instance.acquire('alice', 'chat', model='sql-expert')
        assert lease.backend_id == 'gpu-01'

    async def test_the_base_model_selects_either(self, pool):
        instance, _ = pool([_lora_node('gpu-01'), _lora_node('gpu-02')])
        lease = await instance.acquire('alice', 'chat', model='base-27b')
        assert lease.backend_id in {'gpu-01', 'gpu-02'}

    async def test_an_unknown_adapter_lists_what_is_there(self, pool):
        """Including adapter names, not just base models — otherwise the
        error tells a developer their adapter does not exist when it is
        simply on another node."""
        instance, _ = pool([_lora_node('gpu-01')])
        with pytest.raises(ModelNotAvailableError) as exc:
            await instance.acquire('alice', 'chat', model='nonexistent')
        assert 'sql-expert' in str(exc.value)
        assert 'docs-writer' in str(exc.value)


class TestTheLease:
    async def test_it_carries_the_adapter_that_was_asked_for(self, pool):
        """The one that matters. Sending the base model upstream when an
        adapter was requested serves plausible output from the wrong
        weights, and nothing fails."""
        instance, _ = pool([_lora_node()])
        lease = await instance.acquire('alice', 'chat', model='sql-expert')
        assert lease.model == 'sql-expert'

    async def test_an_unspecified_request_gets_the_base_model(self, pool):
        instance, _ = pool([_lora_node()])
        lease = await instance.acquire('alice', 'chat')
        assert lease.model == 'base-27b'


class TestCapacityIsPerNode:
    async def test_adapters_share_the_node_s_concurrency(self, pool):
        """They share the GPU. Counting them separately would let one node
        accept twice its limit by splitting requests across two adapter
        names."""
        instance, _ = pool([_lora_node('gpu-01', adapters=('sql-expert',))])

        held = [await instance.acquire(f'u{i}', 'chat', model='base-27b') for i in range(8)]
        assert len(held) == 8

        from app.services.ai.schemas import NoBackendAvailableError

        with pytest.raises(NoBackendAvailableError):
            await instance.acquire('late', 'chat', model='sql-expert')


class TestAdvertising:
    async def test_every_adapter_is_offered_as_its_own_model(self, pool):
        """The CLI lists these, so an adapter nobody can see is an adapter
        nobody uses."""
        instance, _ = pool([_lora_node()])
        names = {name for name, _ in await instance.list_available_models('chat')}
        assert names == {'base-27b', 'sql-expert', 'docs-writer'}


class TestRoutingIdentity:
    def test_adding_an_adapter_counts_as_re_pointing(self):
        """The node now answers to a name it did not before, and a sticky
        assignment made under the old set is no longer describing the same
        thing."""
        before = InferenceBackendProvider.routing_map([_lora_node(adapters=())])
        after = InferenceBackendProvider.routing_map([_lora_node(adapters=('sql-expert',))])
        assert before != after

    def test_the_order_of_adapters_does_not(self):
        """Listing the same adapters differently is not a change."""
        one = InferenceBackendProvider.routing_map([_lora_node(adapters=('a', 'b'))])
        other = InferenceBackendProvider.routing_map([_lora_node(adapters=('b', 'a'))])
        assert one == other
