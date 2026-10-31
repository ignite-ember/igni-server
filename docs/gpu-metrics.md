# Everything a vLLM node can tell us

igni currently scrapes three gauges. This is the full set available, what
each one would be worth, and why the rest are not collected yet.

The endpoint is `GET {host}/metrics` — at the server root, **not** under
the `/v1` prefix a node's base URL carries. Prometheus text format.
vLLM's `--api-key` does not protect it (it covers only `/v1`, `/v2` and
`/inference`), so it answers without a token; igni sends the node's token
anyway, for deployments that put a proxy in front of everything.

---

## What we collect today

| Metric | Type | Shown as |
|---|---|---|
| `vllm:num_requests_running` | Gauge | "64 running", and the disagreement with our own lease count |
| `vllm:num_requests_waiting` | Gauge | "5 queued" — also forces the node to **degraded** |
| `vllm:kv_cache_usage_perc` | Gauge | "97.2% used" |

Three gauges, one scrape, no state. The queue is the one that earns its
place: our concurrency limit exists to stop work piling up somewhere we
cannot see, so a non-empty queue means the limit is wrong for that
hardware — or that igni is not the only client.

---

## What we could collect, in rough order of value

### 1. Prefix cache hit rate — *the one we should add next*

| Metric | Type |
|---|---|
| `vllm:prefix_cache_queries` | Counter |
| `vllm:prefix_cache_hits` | Counter |

`hits / queries` is the hit rate. This directly measures **the single
largest design decision in the pool**: sticky per-user routing exists so
a developer's next turn lands on the node still holding their prefix in
KV cache. Right now that claim is asserted and never verified. A hit rate
that collapsed after a config change would be invisible.

Cheap: two counters, one division. Being counters they are cumulative, so
a rate needs two samples — but the *ratio* is meaningful as a lifetime
average without any sampling.

### 2. Preemptions

| Metric | Type |
|---|---|
| `vllm:num_preemptions_total` | Counter |

vLLM preempts a running sequence when it runs out of KV blocks: the work
already done is discarded and redone later. A rising count means the node
is thrashing, and it will show up to users as latency long before it
shows up as an error. Arguably belongs alongside the queue as a
degraded-state trigger.

### 3. Throughput

| Metric | Type |
|---|---|
| `vllm:prompt_tokens_total` | Counter |
| `vllm:generation_tokens_total` | Counter |

Tokens per second, as a rate over two samples. The number a customer
actually wants when deciding whether a GH200 is earning its rent, and the
one that makes "do we need another node" answerable in something other
than request counts.

Needs the previous sample kept between scrapes — the first piece of state
in the metrics path, which is why it is not free.

### 4. Latency distributions

| Metric | Type |
|---|---|
| `vllm:time_to_first_token_seconds` | Histogram |
| `vllm:inter_token_latency_seconds` | Histogram |
| `vllm:e2e_request_latency_seconds` | Histogram |
| `vllm:request_queue_time_seconds` | Histogram |
| `vllm:request_prefill_time_seconds` | Histogram |
| `vllm:request_decode_time_seconds` | Histogram |

TTFT is what a developer experiences as "is it responding". Queue time
separates *we were slow* from *we were waiting*, and prefill vs decode
separates a long prompt from a long answer.

These are the expensive ones: each arrives as a set of `_bucket{le="…"}`
series plus `_sum` and `_count`. Percentiles require interpolating across
buckets, and a p99 from one instantaneous scrape is a lifetime figure,
not a current one. Honest options are the `_sum / _count` average, or
shipping to a real Prometheus and letting it do the maths.

### 5. Per-adapter load

| Metric | Type |
|---|---|
| `vllm:lora_requests_info` | Gauge |

Which LoRA adapters are running and waiting. Relevant now that one node
can serve a base model plus adapters, and the only way to see that one
adapter is starving the others.

Awkward by design: the labels are `running_lora_adapters`,
`waiting_lora_adapters` and `max_lora`, carrying **comma-separated
adapter names inside a label value**, and the metric's *value* is the
current wall-clock time. vLLM's own docs call the design "quite
misguided". Parseable, but not pleasant, and worth waiting to see whether
it changes.

### 6. Request outcomes

| Metric | Type |
|---|---|
| `vllm:request_success_total` | Counter |

Labelled by `finished_reason`: `stop`, `length`, `abort`. A rising
`length` share means answers are being truncated by the token cap — a
quality problem that is otherwise invisible from our side. `abort` counts
clients hanging up.

### 7. Token-count distributions

| Metric | Type |
|---|---|
| `vllm:request_prompt_tokens` | Histogram |
| `vllm:request_generation_tokens` | Histogram |
| `vllm:request_max_num_generation_tokens` | Histogram |

Prompt and completion size distributions. Useful for capacity planning —
mostly the same information a real observability stack would give you.

### 8. KV block lifecycle

| Metric | Type |
|---|---|
| `vllm:kv_block_lifetime_seconds` | Histogram |
| `vllm:kv_block_idle_before_evict_seconds` | Histogram |
| `vllm:kv_block_reuse_gap_seconds` | Histogram |

How long cache blocks survive, and how long between reuses. This is the
deep version of the sticky-routing question — if reuse gaps exceed block
lifetimes, the pinning is not buying anything. Specialist, and only
meaningful once the hit rate above suggests a problem.

### 9. Configuration

| Metric | Type |
|---|---|
| `vllm:cache_config_info` | Gauge (Info) |

The node's own cache configuration, as labels. Would let the portal show
a node's real block size and `gpu_memory_utilization` rather than trusting
what somebody typed.

---

## Not available

Removed in vLLM v1, so do not plan around them:

- `vllm:num_requests_swapped` and `vllm:cpu_cache_usage_perc` — CPU
  swapping is gone.
- `vllm:time_in_queue_requests` — superseded by
  `vllm:request_queue_time_seconds`.
- `vllm:tokens_total` — documented but never implemented.

Also not on this endpoint: **GPU temperature, power draw, and utilisation
percentage**. Those come from the driver, via `nvidia-smi` or DCGM
exporter, not from vLLM. A customer wanting them runs DCGM alongside; we
could scrape it, but it is a second endpoint on a different port with a
different exposition, and it is not something vLLM knows.

---

## Labels

Nearly everything carries `model_name`. On a node serving several models
that means one series per model, which is why igni sums counts across
series and takes the max of the cache fraction — the question being asked
is about the GPU, not about one of its models.

---

## Why not just point Prometheus at it

For a customer who already runs Prometheus and Grafana, that is the right
answer, and nothing here stops them: the endpoint is theirs.

What the portal is for is the customer who does not. A self-hosted
deployment where somebody has been handed three GPUs and a deadline
should be able to answer "is it up, is it busy, do I need another one"
without standing up an observability stack first. That is the line: the
portal shows the handful of numbers that answer those three questions,
and gets out of the way of anyone who wants the rest properly.
