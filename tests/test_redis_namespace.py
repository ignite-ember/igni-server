"""Redis keys stay inside igni server's namespace.

igni server can share one physical Redis with other services, separated
by a key prefix, and a key outside it is wrong silently: two services
writing one key corrupt each other's state with nothing naming the cause.
The rule lives in ``app/db/redis/keys.py``; this holds the code to it.
"""

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
        assert key('backends', 'inflight') == 'igni:server:backends:inflight'


class TestThePoolStaysInside:
    def test_every_pool_key_is_namespaced(self):
        instance = InferenceBackendPool()
        for attr in ('_inflight_prefix', '_sticky_prefix', '_routing_key'):
            assert getattr(instance, attr).startswith(NAMESPACE)


class TestTheRegistryStaysInside:
    def test_registered_nodes_are_namespaced(self):
        assert REGISTRY_PREFIX.startswith(NAMESPACE)
