"""What the GPU says about itself.

Everything else on the inference page is our own bookkeeping. ``in_flight``
counts leases *we* handed out, which is exact for traffic igni routed and
blind to everything else: somebody curling the node directly, another
service sharing it, or requests in flight from before we knew it existed.
On a shared node the meter reads green while the card is saturated.

vLLM publishes the truth on ``/metrics`` in Prometheus text format, and
three gauges answer the questions the lease count cannot:

* ``num_requests_running`` — sequences actually decoding right now.
* ``num_requests_waiting`` — requests queued *inside* the node. This is
  the important one. Our concurrency limit exists to stop work queueing
  somewhere we cannot see or balance it, so a non-empty queue means the
  limit is set too high for this hardware, and no amount of watching our
  own gauge would reveal it.
* ``kv_cache_usage_perc`` — how full the KV cache is. The number that
  says whether sticky routing is buying anything, and the one that goes
  up before a node starts evicting and recomputing prefixes.

## Two names for the cache gauge

It was ``gpu_cache_usage_perc`` and became ``kv_cache_usage_perc``. Both
are accepted rather than pinning a vLLM version, because a customer's
fleet is quite likely to span an upgrade.

## Not authenticated, and that is not our doing

vLLM's ``--api-key`` only guards the ``/v1``, ``/v2`` and ``/inference``
prefixes, so ``/metrics`` answers without one. We send the node's token
anyway when it has one: a customer who has put a reverse proxy in front
of the whole server will need it, and a node that ignores the header is
unharmed by receiving it.
"""

from __future__ import annotations

import logging

import httpx

from app.services.ai.schemas import NodeMetrics

logger = logging.getLogger(__name__)

# ``running`` and ``waiting`` are counts; the cache gauge is a 0–1
# fraction. Listed with the alternative spellings vLLM has used.
_RUNNING = ('vllm:num_requests_running',)
_WAITING = ('vllm:num_requests_waiting',)
_CACHE = ('vllm:kv_cache_usage_perc', 'vllm:gpu_cache_usage_perc')

# Prefix-cache counters. Their ratio is the one number that actually
# checks the pool's largest design decision: sticky per-user routing
# exists so a developer's next turn lands on the node still holding
# their prefix. Without this the claim is asserted and never verified.
_CACHE_QUERIES = ('vllm:prefix_cache_queries', 'vllm:prefix_cache_queries_total')
_CACHE_HITS = ('vllm:prefix_cache_hits', 'vllm:prefix_cache_hits_total')

# Sequences vLLM threw away and will redo because it ran out of KV
# blocks. Thrashing shows up here long before it shows up as an error.
_PREEMPTIONS = ('vllm:num_preemptions_total', 'vllm:num_preemptions')


def metrics_url(base_url: str) -> str:
    """Where ``/metrics`` lives, given an OpenAI base URL.

    At the server root, not under the API prefix: a node configured as
    ``http://gpu:8000/v1`` serves metrics from ``http://gpu:8000/metrics``.
    Asking the wrong path is a 404 that looks like "this node has no
    metrics", so the ``/v1`` suffix is stripped rather than appended to.
    """
    root = base_url.rstrip('/')
    if root.endswith('/v1'):
        root = root[: -len('/v1')]
    return f'{root}/metrics'


def parse(text: str) -> NodeMetrics:
    """Read the three gauges out of Prometheus text format.

    Values are summed across label sets rather than taking the first. A
    node serving several models reports a series per model, and the
    question being asked — how busy is this GPU — is about the whole
    process, not one of its models.
    """
    running = waiting = 0.0
    queries = hits = preemptions = 0.0
    cache: float | None = None
    saw_running = saw_waiting = False
    saw_queries = saw_hits = saw_preemptions = False

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name, _, value = line.rpartition(' ')
        metric = name.split('{', 1)[0].strip()
        try:
            number = float(value)
        except ValueError:
            continue

        if metric in _RUNNING:
            running += number
            saw_running = True
        elif metric in _WAITING:
            waiting += number
            saw_waiting = True
        elif metric in _CACHE:
            # A fraction, so summing across series would be meaningless.
            # The busiest one is the one that matters.
            cache = number if cache is None else max(cache, number)
        elif metric in _CACHE_QUERIES:
            queries += number
            saw_queries = True
        elif metric in _CACHE_HITS:
            hits += number
            saw_hits = True
        elif metric in _PREEMPTIONS:
            preemptions += number
            saw_preemptions = True

    return NodeMetrics(
        running=int(running) if saw_running else None,
        waiting=int(waiting) if saw_waiting else None,
        kv_cache_percent=round(cache * 100, 1) if cache is not None else None,
        # A lifetime ratio, not a rate: these are cumulative counters, and
        # the proportion is meaningful from a single scrape in a way a
        # per-second figure would not be. Guarded against a node that has
        # served nothing yet, where 0/0 is not 0%.
        prefix_cache_hit_percent=(round(hits / queries * 100, 1) if saw_queries and saw_hits and queries > 0 else None),
        preemptions=int(preemptions) if saw_preemptions else None,
    )


async def fetch(*, base_url: str, api_key: str | None, timeout: float) -> NodeMetrics | None:
    """Scrape one node. None when it has nothing to say.

    Every failure is None rather than an exception: metrics are a richer
    view laid over a page that already works without them, and a node
    that is not vLLM — or is behind a proxy that hides ``/metrics`` —
    must not lose its row.
    """
    headers = {'Authorization': f'Bearer {api_key}'} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(metrics_url(base_url), headers=headers)
    except httpx.HTTPError as exc:
        logger.debug('No metrics from %s: %s', base_url, exc)
        return None

    if response.status_code != 200:
        return None

    metrics = parse(response.text)
    # An endpoint that answered but held none of our gauges is not a vLLM
    # metrics endpoint, and reporting zeros from it would be worse than
    # reporting nothing.
    return metrics if metrics.reported else None
