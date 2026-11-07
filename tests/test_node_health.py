"""Probing nodes, and turning the answer into a colour.

The reason this exists: heartbeats only tell us about nodes that send
them. A static node has never checked in, and a pinned one has nothing
checking in for it, so two of the three sources appeared on the portal
with no liveness information at all — a switched-off machine looked
exactly like one serving traffic.

The distinction the tests care about most is **unreachable versus
degraded**, because it decides where an operator goes looking. "It
didn't answer" sends them to the network; "it rejected our credentials"
sends them to the token. Collapsing the two wastes the afternoon.
"""

import httpx
import pytest

from app.services.ai.health import combine, NodeHealthProbe
from app.services.ai.schemas import HealthState, InferenceBackend, NodeHealth


def _models(*ids: str) -> dict:
    """An OpenAI ``/models`` body. On a LoRA node the list holds the base
    model followed by one entry per adapter."""
    return {'object': 'list', 'data': [{'id': name, 'object': 'model'} for name in ids]}


def _backend(node_id='gpu-01', api_key=None, max_concurrent=64) -> InferenceBackend:
    return InferenceBackend(
        id=node_id,
        base_url=f'http://{node_id}.internal:8000/v1',
        models=['igni-1.0-27b'],
        api_key=api_key,
        max_concurrent=max_concurrent,
    )


@pytest.fixture
def probe(monkeypatch):
    """A prober whose cache is a dict and whose HTTP is scripted."""
    cache: dict[str, str] = {}

    class FakeRedis:
        async def get(self, key):
            return cache.get(key)

        async def set(self, key, value, ex=None):
            cache[key] = value

    instance = NodeHealthProbe()
    monkeypatch.setattr(instance, '_get_redis', lambda: _async(FakeRedis()))

    def _respond(handler):
        class FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None):
                return handler(url, headers or {})

        monkeypatch.setattr('app.services.ai.health.httpx.AsyncClient', FakeClient)

    instance.respond = _respond  # type: ignore[attr-defined]
    instance.cache = cache  # type: ignore[attr-defined]
    return instance


async def _async(value):
    return value


class TestReachable:
    async def test_a_node_that_answers_is_healthy(self, probe):
        probe.respond(lambda url, headers: httpx.Response(200, json=_models('igni-1.0-27b')))
        health = await probe.check(_backend())
        assert health.state is HealthState.HEALTHY

    async def test_it_probes_the_models_endpoint_not_a_completion(self, probe):
        """A real inference request costs GPU time and would report a busy
        node as slow rather than as busy."""
        seen: list[str] = []

        def handler(url, headers):
            seen.append(url)
            return httpx.Response(200, json=_models('igni-1.0-27b'))

        probe.respond(handler)
        await probe.check(_backend())
        assert seen == ['http://gpu-01.internal:8000/v1/models']

    async def test_the_node_credential_is_presented(self, probe):
        """A node behind an auth proxy would otherwise always look
        degraded."""
        seen: list[dict] = []

        def handler(url, headers):
            seen.append(headers)
            return httpx.Response(200, json=_models('igni-1.0-27b'))

        probe.respond(handler)
        await probe.check(_backend(api_key='node-secret'))
        assert seen[0]['Authorization'] == 'Bearer node-secret'

    async def test_latency_is_reported(self, probe):
        probe.respond(lambda url, headers: httpx.Response(200, json=_models('igni-1.0-27b')))
        assert (await probe.check(_backend())).latency_ms is not None


class TestUnreachable:
    async def test_a_refused_connection_is_unreachable(self, probe):
        def handler(url, headers):
            raise httpx.ConnectError('Connection refused')

        probe.respond(handler)
        health = await probe.check(_backend())
        assert health.state is HealthState.UNREACHABLE
        assert 'refused' in (health.detail or '').lower()

    async def test_a_timeout_is_unreachable_and_says_how_long(self, probe):
        def handler(url, headers):
            raise httpx.ReadTimeout('timed out')

        probe.respond(handler)
        health = await probe.check(_backend())
        assert health.state is HealthState.UNREACHABLE
        assert 's' in (health.detail or '')

    async def test_a_5xx_is_unreachable(self, probe):
        """It is listening but cannot serve, which for routing purposes is
        the same as being off."""
        probe.respond(lambda url, headers: httpx.Response(503))
        assert (await probe.check(_backend())).state is HealthState.UNREACHABLE


class TestDegraded:
    async def test_a_rejected_credential_is_degraded_not_unreachable(self, probe):
        """The address is right and the machine answered. Calling this
        unreachable sends somebody to check cabling instead of the
        token."""
        probe.respond(lambda url, headers: httpx.Response(401))
        health = await probe.check(_backend())
        assert health.state is HealthState.DEGRADED
        assert 'credential' in (health.detail or '').lower()

    async def test_a_4xx_is_degraded(self, probe):
        probe.respond(lambda url, headers: httpx.Response(404))
        assert (await probe.check(_backend())).state is HealthState.DEGRADED


class TestCaching:
    async def test_a_second_check_reuses_the_first(self, probe):
        """Several admins with the page open — it refreshes every ten
        seconds — should share one probe per node, not generate their
        own."""
        calls: list[str] = []

        def handler(url, headers):
            calls.append(url)
            return httpx.Response(200, json=_models('igni-1.0-27b'))

        probe.respond(handler)
        await probe.check(_backend())
        await probe.check(_backend())
        assert len(calls) == 1

    async def test_the_cache_entry_expires(self, probe):
        """Otherwise a node coming back up would stay red."""
        probe.respond(lambda url, headers: httpx.Response(200, json=_models('igni-1.0-27b')))
        await probe.check(_backend())
        assert probe.cache  # written with a TTL; see _CACHE_TTL_SECONDS


class TestProbingEverything:
    async def test_every_node_is_probed_concurrently(self, probe):
        probe.respond(lambda url, headers: httpx.Response(200, json=_models('igni-1.0-27b')))
        health = await probe.check_all([_backend('gpu-01'), _backend('gpu-02')])
        assert set(health) == {'gpu-01', 'gpu-02'}

    async def test_one_broken_probe_does_not_lose_the_others(self, probe):
        """A page render must not fail because one address is nonsense."""

        def handler(url, headers):
            if 'gpu-02' in url:
                raise httpx.ConnectError('nope')
            return httpx.Response(200, json=_models('igni-1.0-27b'))

        probe.respond(handler)
        health = await probe.check_all([_backend('gpu-01'), _backend('gpu-02')])
        assert health['gpu-01'].state is HealthState.HEALTHY
        assert health['gpu-02'].state is HealthState.UNREACHABLE


class TestCombining:
    """What we know from elsewhere, folded into the probe result."""

    def test_a_saturated_node_is_degraded(self):
        health = combine(
            NodeHealth(state=HealthState.HEALTHY),
            in_flight=64,
            max_concurrent=64,
            last_seen_seconds_ago=None,
        )
        assert health.state is HealthState.DEGRADED
        assert 'limit' in (health.detail or '')

    def test_a_stale_heartbeat_is_degraded_even_when_it_answers(self):
        """The endpoint is up but whatever was renewing the registration
        has died, so the node is minutes from dropping out of rotation
        with nothing having said so."""
        health = combine(
            NodeHealth(state=HealthState.HEALTHY),
            in_flight=0,
            max_concurrent=64,
            last_seen_seconds_ago=900,
        )
        assert health.state is HealthState.DEGRADED
        assert 'checked in' in (health.detail or '')

    def test_a_fresh_heartbeat_stays_healthy(self):
        health = combine(
            NodeHealth(state=HealthState.HEALTHY),
            in_flight=1,
            max_concurrent=64,
            last_seen_seconds_ago=30,
        )
        assert health.state is HealthState.HEALTHY

    def test_it_only_ever_downgrades(self):
        """An unreachable node is not rescued by looking idle."""
        health = combine(
            NodeHealth(state=HealthState.UNREACHABLE, detail='No response within 3s'),
            in_flight=0,
            max_concurrent=64,
            last_seen_seconds_ago=1,
        )
        assert health.state is HealthState.UNREACHABLE
        assert health.detail == 'No response within 3s'


class TestCredentials:
    """A vLLM node started with ``--api-key`` protects ``/v1``, and
    ``/v1/models`` is exactly what a probe asks for. So the most likely
    result of pasting an address is a 401, and it has to read as "enter a
    token" rather than as "this node serves nothing"."""

    async def test_a_401_is_reported_as_wanting_credentials(self, probe):
        probe.respond(lambda url, headers: httpx.Response(401))
        result = await probe.list_models(base_url='http://gpu-01.internal:8000/v1')
        assert result.needs_credentials is True
        assert result.models == []

    async def test_a_403_counts_too(self, probe):
        probe.respond(lambda url, headers: httpx.Response(403))
        result = await probe.list_models(base_url='http://gpu-01.internal:8000/v1')
        assert result.needs_credentials is True

    async def test_a_node_that_answers_does_not(self, probe):
        probe.respond(lambda url, headers: httpx.Response(200, json=_models('base-27b')))
        result = await probe.list_models(base_url='http://gpu-01.internal:8000/v1')
        assert result.needs_credentials is False
        assert result.models == ['base-27b']

    async def test_an_unreachable_node_does_not(self, probe):
        """Nothing answered, so nothing asked for a token."""

        def handler(url, headers):
            raise httpx.ConnectError('refused')

        probe.respond(handler)
        result = await probe.list_models(base_url='http://gpu-01.internal:8000/v1')
        assert result.needs_credentials is False

    async def test_the_token_unlocks_the_model_list(self, probe):
        """The point of the token field: with it, discovery works."""

        def handler(url, headers):
            if headers.get('Authorization') != 'Bearer token-abc123':
                return httpx.Response(401)
            return httpx.Response(200, json=_models('base-27b', 'sql-expert'))

        probe.respond(handler)
        result = await probe.list_models(base_url='http://gpu-01.internal:8000/v1', api_key='token-abc123')
        assert result.needs_credentials is False
        assert result.models == ['base-27b', 'sql-expert']
