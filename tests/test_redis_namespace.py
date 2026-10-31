"""Redis keys stay inside this service's namespace.

igni server, igni__code-index and igni__artifacts are designed to run
against one physical Redis and one physical Postgres, separated
logically. For Postgres that separation is a database name and hard to
get wrong. For Redis it is a key prefix, and getting it wrong is
silent.

It has now been got wrong twice, in different ways:

* **the inference pool.** Both this service and igni__code-index run
  one, and both call one of its pools ``codeindex``, so their in-flight
  lease sets were the same key. Two services incrementing one
  concurrency gauge makes every node look twice as busy as it is, and
  the pool eventually decides a healthy cluster is full — surfacing as
  latency and capacity errors with nothing naming the cause;
* **the document keys.** Unprefixed ``igni:doc:…`` in
  ``document_room.py`` and ``document_draft.py``, which
  ``igni__artifacts`` held a copy of. Those modules have since moved
  there outright, and the assertion with them.

Neither was a mistake anybody made twice in the same place, which is
why the rule lives in ``app/db/redis/keys.py`` and the enforcement
lives here: the next module to reach for Redis will be written by
somebody who has not read either story.

So this file asserts the boundary rather than trusting that three
deployments were configured onto different logical databases — which is
the configuration an operator consolidates when asked to run fewer
Redis instances.
"""

import pytest

from app.db.redis.keys import key, NAMESPACE
from app.services.ai.backend_pool import InferenceBackendPool
from app.services.ai.node_registry import _KEY_PREFIX as REGISTRY_PREFIX


class TestNamespace:
    def test_it_names_this_service(self):
        assert NAMESPACE == 'igni:server:'

    def test_it_is_a_prefix_not_a_word(self):
        """Trailing colon, or ``igni:server`` and ``igni:serverless``
        would share a scan prefix."""
        assert NAMESPACE.endswith(':')

    def test_keys_are_built_from_parts(self):
        assert key('backends', 'chat', 'inflight') == 'igni:server:backends:chat:inflight'


class TestThePoolStaysInside:
    """The keys that actually collided."""

    @pytest.mark.parametrize('pool', ['chat', 'codeindex'])
    def test_every_pool_key_is_namespaced(self, pool):
        instance = InferenceBackendPool(pool)
        for attr in ('_inflight_prefix', '_sticky_prefix', '_routing_key'):
            assert getattr(instance, attr).startswith(NAMESPACE)

    def test_the_codeindex_pool_differs_across_services(self):
        """The one that mattered: this exact string is also built by the
        other service, and the namespace is the only thing keeping the two
        apart."""
        built = InferenceBackendPool('codeindex')._inflight_prefix
        assert built == 'igni:server:backends:codeindex:inflight:'


class TestTheRegistryStaysInside:
    def test_registered_nodes_are_namespaced(self):
        assert REGISTRY_PREFIX.startswith(NAMESPACE)


# ``TestTheDocumentKeysStayInside`` was here.
#
# It held the second collision this file records: ``document_room.py``
# and ``document_draft.py`` built ``igni:doc:…`` with no service
# prefix, and ``igni__artifacts`` held a copy of both. Those modules
# have gone to that repository with the rest of the documents feature,
# and the assertion went with them —
# ``igni__artifacts/tests/test_redis_namespace.py`` is where the
# document keys are held to ``igni:artifacts:`` now.
#
# The story stays in this module's docstring because the *rule* is
# this file's: it is the second time an unprefixed key reached
# production-shaped code, and the next module to want Redis will be
# written by somebody who has read neither story.
