"""Reading a node's own gauges off its ``/metrics`` endpoint.

Everything else on the inference page is our bookkeeping. ``in_flight``
counts leases *we* issued — exact for traffic igni routed, and blind to
everything else: somebody curling the node directly, another service
sharing the GPU, requests in flight from before we knew it existed. On a
shared node the meter reads green while the card is saturated.

The gauge that earns this feature is ``num_requests_waiting``. Our
concurrency limit exists precisely to stop work queueing somewhere we
can neither see nor balance, so a non-empty queue means the limit is set
too high for the hardware — and watching our own counter could never
reveal it.
"""

import httpx
import pytest

from app.services.ai import metrics as node_metrics
from app.services.ai.health import combine
from app.services.ai.schemas import HealthState, NodeHealth, NodeMetrics

# Trimmed from a real vLLM /metrics response: HELP and TYPE comments,
# label sets, and float-formatted counts.
VLLM_METRICS = """\
# HELP vllm:num_requests_running Number of requests currently running on GPU.
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running{model_name="meta-llama/Llama-3-8B"} 7.0
# HELP vllm:num_requests_waiting Number of requests waiting to be processed.
# TYPE vllm:num_requests_waiting gauge
vllm:num_requests_waiting{model_name="meta-llama/Llama-3-8B"} 3.0
# HELP vllm:kv_cache_usage_perc KV-cache usage. 1 means 100 percent usage.
# TYPE vllm:kv_cache_usage_perc gauge
vllm:kv_cache_usage_perc{model_name="meta-llama/Llama-3-8B"} 0.6234
# HELP vllm:prompt_tokens_total Number of prefill tokens processed.
# TYPE vllm:prompt_tokens_total counter
vllm:prompt_tokens_total{model_name="meta-llama/Llama-3-8B"} 918273.0
# HELP vllm:prefix_cache_queries Prefix cache lookups.
# TYPE vllm:prefix_cache_queries counter
vllm:prefix_cache_queries{model_name="meta-llama/Llama-3-8B"} 1000.0
# HELP vllm:prefix_cache_hits Prefix cache hits.
# TYPE vllm:prefix_cache_hits counter
vllm:prefix_cache_hits{model_name="meta-llama/Llama-3-8B"} 812.0
# HELP vllm:num_preemptions_total Cumulative preemptions.
# TYPE vllm:num_preemptions_total counter
vllm:num_preemptions_total{model_name="meta-llama/Llama-3-8B"} 4.0
"""


class TestTheUrl:
    def test_metrics_live_at_the_root_not_under_v1(self):
        """A node configured as .../v1 serves metrics from the server
        root. Appending to the API prefix gives a 404 that reads as "this
        node has no metrics"."""
        assert node_metrics.metrics_url('http://gpu:8000/v1') == 'http://gpu:8000/metrics'

    def test_a_trailing_slash_is_handled(self):
        assert node_metrics.metrics_url('http://gpu:8000/v1/') == 'http://gpu:8000/metrics'

    def test_a_base_without_v1_is_left_alone(self):
        """Some deployments front vLLM on a bare host."""
        assert node_metrics.metrics_url('http://gpu:8000') == 'http://gpu:8000/metrics'


class TestParsing:
    def test_it_reads_the_gauges(self):
        parsed = node_metrics.parse(VLLM_METRICS)
        assert parsed.running == 7
        assert parsed.waiting == 3
        assert parsed.kv_cache_percent == 62.3

    def test_the_prefix_cache_hit_rate_is_a_ratio_of_the_counters(self):
        """The one number that checks the pool's largest design decision.
        Sticky routing exists so a developer's next turn lands on the node
        still holding their prefix; without this it was asserted and never
        measured."""
        assert node_metrics.parse(VLLM_METRICS).prefix_cache_hit_percent == 81.2

    def test_preemptions_are_read(self):
        assert node_metrics.parse(VLLM_METRICS).preemptions == 4

    def test_a_node_that_has_served_nothing_reports_no_hit_rate(self):
        """0/0 is not 0%, and showing a red bar on an idle node would be
        a lie."""
        text = 'vllm:prefix_cache_queries{m="a"} 0.0\nvllm:prefix_cache_hits{m="a"} 0.0\n'
        assert node_metrics.parse(text).prefix_cache_hit_percent is None

    def test_comments_and_counters_are_ignored(self):
        """The response is mostly HELP/TYPE lines and histograms."""
        assert node_metrics.parse(VLLM_METRICS).running == 7

    def test_the_older_cache_gauge_name_is_accepted(self):
        """It was gpu_cache_usage_perc before it was kv_cache_usage_perc,
        and a customer's fleet may well span the upgrade."""
        text = 'vllm:gpu_cache_usage_perc{model_name="m"} 0.5\n'
        assert node_metrics.parse(text).kv_cache_percent == 50.0

    def test_series_are_summed_across_models(self):
        """A node serving several models reports one series each, and the
        question is how busy the GPU is — not one of its models."""
        text = 'vllm:num_requests_running{model_name="a"} 2.0\nvllm:num_requests_running{model_name="b"} 3.0\n'
        assert node_metrics.parse(text).running == 5

    def test_the_cache_gauge_takes_the_max_not_the_sum(self):
        """It is a fraction. Adding two of them produces a number that
        means nothing."""
        text = 'vllm:kv_cache_usage_perc{model_name="a"} 0.4\nvllm:kv_cache_usage_perc{model_name="b"} 0.8\n'
        assert node_metrics.parse(text).kv_cache_percent == 80.0

    def test_a_zero_queue_is_reported_as_zero_not_absent(self):
        """ "Nothing queued" and "we could not ask" are different, and only
        one of them is good news."""
        text = 'vllm:num_requests_waiting{model_name="m"} 0.0\n'
        parsed = node_metrics.parse(text)
        assert parsed.waiting == 0
        assert parsed.reported is True

    def test_nothing_recognisable_reports_nothing(self):
        assert node_metrics.parse('# nothing here\nfoo_bar 1.0\n').reported is False

    def test_a_malformed_value_does_not_lose_the_rest(self):
        text = 'vllm:num_requests_running{m="a"} nonsense\nvllm:num_requests_waiting{m="a"} 4.0\n'
        parsed = node_metrics.parse(text)
        assert parsed.running is None
        assert parsed.waiting == 4


class TestFetching:
    @pytest.fixture
    def responds(self, monkeypatch):
        def _with(handler):
            class FakeClient:
                def __init__(self, *a, **k):
                    pass

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                async def get(self, url, headers=None):
                    return handler(url, headers or {})

            monkeypatch.setattr('app.services.ai.metrics.httpx.AsyncClient', FakeClient)

        return _with

    async def test_it_scrapes_the_gauges(self, responds):
        responds(lambda url, headers: httpx.Response(200, text=VLLM_METRICS))
        result = await node_metrics.fetch(base_url='http://gpu:8000/v1', api_key=None, timeout=3.0)
        assert result is not None
        assert result.running == 7

    async def test_the_token_is_sent_when_there_is_one(self, responds):
        """vLLM leaves /metrics unauthenticated, but a node behind a
        reverse proxy will want the header — and one that ignores it is
        unharmed by receiving it."""
        seen: list[str | None] = []

        def handler(url, headers):
            seen.append(headers.get('Authorization'))
            return httpx.Response(200, text=VLLM_METRICS)

        responds(handler)
        await node_metrics.fetch(base_url='http://gpu:8000/v1', api_key='node-secret', timeout=3.0)
        assert seen == ['Bearer node-secret']

    async def test_a_node_without_metrics_is_not_a_failure(self, responds):
        """Not every node is vLLM, and the page worked before this
        existed."""
        responds(lambda url, headers: httpx.Response(404))
        assert await node_metrics.fetch(base_url='http://gpu:8000/v1', api_key=None, timeout=3.0) is None

    async def test_an_unrelated_metrics_endpoint_reports_nothing(self, responds):
        """Answering with somebody else's metrics is not answering. Zeros
        from it would be worse than an empty column."""
        responds(lambda url, headers: httpx.Response(200, text='process_cpu_seconds_total 12.0\n'))
        assert await node_metrics.fetch(base_url='http://gpu:8000/v1', api_key=None, timeout=3.0) is None

    async def test_an_unreachable_node_is_not_a_failure(self, responds):
        def handler(url, headers):
            raise httpx.ConnectError('refused')

        responds(handler)
        assert await node_metrics.fetch(base_url='http://gpu:8000/v1', api_key=None, timeout=3.0) is None


class TestQueueBeatsOurCounter:
    """The reason for the whole feature."""

    def test_a_queued_node_is_degraded_even_when_we_think_it_is_idle(self):
        """Our lease count says one request; the node says three are
        waiting. Somebody else is using this GPU, and only the node
        knows."""
        health = combine(
            NodeHealth(state=HealthState.HEALTHY),
            in_flight=1,
            max_concurrent=64,
            last_seen_seconds_ago=None,
            metrics=NodeMetrics(running=64, waiting=3, kv_cache_percent=97.0),
        )
        assert health.state is HealthState.DEGRADED
        assert 'queued on the node itself' in (health.detail or '')

    def test_an_empty_queue_leaves_it_healthy(self):
        health = combine(
            NodeHealth(state=HealthState.HEALTHY),
            in_flight=1,
            max_concurrent=64,
            last_seen_seconds_ago=None,
            metrics=NodeMetrics(running=1, waiting=0, kv_cache_percent=12.0),
        )
        assert health.state is HealthState.HEALTHY

    def test_a_node_reporting_nothing_falls_back_to_our_counter(self):
        """No metrics is not a reason to lose the capacity check."""
        health = combine(
            NodeHealth(state=HealthState.HEALTHY),
            in_flight=64,
            max_concurrent=64,
            last_seen_seconds_ago=None,
            metrics=None,
        )
        assert health.state is HealthState.DEGRADED
        assert 'concurrency limit' in (health.detail or '')

    def test_it_still_only_downgrades(self):
        health = combine(
            NodeHealth(state=HealthState.UNREACHABLE, detail='No response within 3s'),
            in_flight=0,
            max_concurrent=64,
            last_seen_seconds_ago=None,
            metrics=NodeMetrics(running=0, waiting=0),
        )
        assert health.state is HealthState.UNREACHABLE
