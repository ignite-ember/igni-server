# Starting and stopping GPU clusters

Two ways to tell igni about your inference hardware. Use both if it
suits you — they merge.

**Static configuration** describes hardware that is always on. A JSON
array in `inference_backends_path` (a ConfigMap or a bind mount) or
`inference_backends` (the same JSON inline). Editing it is a
config change and, in Kubernetes, a rollout.

**Registration** is for hardware that comes and goes. A node announces
itself over HTTP when it starts serving and is removed when it stops. No
config change, no restart, no rollout.

The second exists because the first is the wrong shape for how GPUs are
actually bought. A team spins up four H100s for a Tuesday load test. An
autoscaler replaces a spot node at 3am. A cluster is stopped overnight so
nobody pays for it while everyone is asleep. Editing a ConfigMap and
waiting for a rollout is not a workflow anyone will run twice.

---

## Enabling registration

Set `node_registration_token` to a secret you generate. Leave it unset
and the registration endpoints do not exist — a deployment that describes
its GPUs statically should not be running an API that accepts new
inference targets.

It is a secret of its own, shared with nothing else. It goes into
Terraform, cloud-init and start-up scripts — a much wider blast radius
than a secret only our own services hold — so it has to be rotatable
without touching anything else.

Guard it accordingly. Anyone holding it can add a machine that every
developer's prompts are sent to.

---

## The three calls

All three take `X-Igni-Node-Token: <node_registration_token>`.

### Start

```bash
curl -fsS -X POST https://igni.acme.internal/v1/nodes \
  -H "X-Igni-Node-Token: $IGNI_NODE_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
        "id": "gpu-burst-01",
        "base_url": "http://gpu-burst-01.internal:8000/v1",
        "model": "igni-1.0-27b",
        "max_concurrent": 64
      }'
```

Call it once vLLM is actually answering, not when the machine boots —
between those two moments the node is in rotation and cannot serve.

| Field | Notes |
|---|---|
| `id` | Stable per node. Re-registering the same id replaces the entry, which is how a node moves address after being rescheduled. `A-Za-z0-9._-` only. |
| `base_url` | The OpenAI-compatible endpoint. Must be absolute. |
| `model` | The model this node serves. |
| `max_concurrent` | Roughly the vLLM instance's `--max-num-seqs`. Going over does not fail — it queues inside the node, where igni can no longer see or balance it. |
| `api_key` | The node's own token, if it has one. vLLM sets this with `--api-key <token>` or `VLLM_API_KEY`. |
| `multimodal` | Whether the model accepts image input. Chat only. |

**409** means the id is already in your static configuration. That is
your own deliberate entry and registration will not shadow it — pick a
different id, or remove it from `inference_backends`.

### Heartbeat

```bash
curl -fsS -X POST https://igni.acme.internal/v1/nodes/gpu-burst-01/heartbeat \
  -H "X-Igni-Node-Token: $IGNI_NODE_TOKEN"
```

On a timer, at the interval the register response gives you in
`heartbeat_interval_seconds` (currently 60). Read it from the response
rather than hardcoding it, so a later release can change it without
breaking your loop.

A registration that stops being refreshed expires and the node leaves
rotation on its own. **This is the part that matters**: your stop job
handles a graceful shutdown, but a spot reclaim, a kernel panic and a
severed network never get to run it. Without expiry, a dead machine stays
in the list and fails every request routed to it until somebody notices.

**404** means the registration already expired. Register again — a
heartbeat carries only an id, and igni needs the address and capacity
back before it can route to the node.

### Stop

```bash
curl -fsS -X DELETE https://igni.acme.internal/v1/nodes/gpu-burst-01 \
  -H "X-Igni-Node-Token: $IGNI_NODE_TOKEN"
```

Idempotent — deleting something already gone is a 204, so a stop job that
retries after a timeout does not fail the second time.

Requests already in flight against the node finish against it; anything
that does fail is retried elsewhere. Drain first if you want to be
careful: deregister, wait out your longest request, then shut down.

---

## Tokens

vLLM takes a static token with `--api-key` (or the `VLLM_API_KEY`
environment variable):

```bash
vllm serve <model> --api-key token-abc123
```

Put the same value in `api_key` when you register or add the node, and
igni sends it as `Authorization: Bearer …` on every request — including
the health probe, since vLLM's `--api-key` protects the whole `/v1`
prefix and `/v1/models` is what we probe. Adding a token-protected node
without its token shows up on the portal as *"That node wants a token"*
rather than as an empty model list.

**It is not a complete answer to securing a node.** vLLM only
authenticates the `/v1`, `/v2` and `/inference` prefixes; other paths on
the same server — notably `/invocations`, which exposes the same
inference — are left open. On a private network that is usually fine. If
the node is reachable from anywhere else, put it behind a reverse proxy
or gateway and treat `--api-key` as defence in depth rather than the
defence.

---

## Watching it

**Portal → Inference** (admin only) shows every node: where
it is, what it serves, how many requests are in flight against it, its
capacity, and — for registered nodes — how long ago it last checked in.

Two sets of numbers sit side by side, and the difference between them is
the point.

**In flight** is igni's own count of outstanding leases. It is exact for
traffic we routed and blind to everything else — somebody calling the
node directly, another service sharing the GPU, requests in flight from
before we knew the node existed.

**On the node** is what the node says about itself, scraped from
`/metrics`. `queued` is the one to watch: igni's concurrency limit exists
to stop work piling up somewhere it can no longer see or balance, so a
non-empty queue means `max_concurrent` is set too high for that hardware
— or that igni is not the only client. A node reporting a queue is marked
degraded regardless of what our own counter says. `KV cache` shows how
full the prefix cache is, which is what sticky routing is protecting.

Where the two disagree the row shows both, because the disagreement is
information: *node says 64* under *4 / 64* means fifty other requests
came from somewhere else.

Nodes that are not vLLM simply show `—` here; the rest of the page is
unaffected.

The number to watch is utilization. A cluster sitting at its limit is a
cluster that needs another node, and that is the decision the page exists
to support. Per-node rows answer the follow-up: which one.

The page also tells you whether registration is enabled at all, because
"nothing has registered" and "this deployment does not accept
registrations" look identical otherwise and call for completely different
fixes.

---

## How the pieces interact

**Static wins.** An id in your configuration cannot be registered over.

**Adding a node costs nothing.** igni pins each developer to a node so
its KV cache stays warm across their conversation, and adding hardware
does not disturb existing pins — the new node simply becomes the least
loaded and starts taking new assignments. Removing one does not disturb
them either; anyone pinned to it falls through to the next pick. Only
re-pointing an existing id at different hardware drops the pins for that
id, because those promise a warm cache that is no longer there.

**A Redis restart empties the registry.** Every live node re-announces on
its next heartbeat, so recovery is one interval; statically configured
nodes are unaffected. That is the trade for never routing to a machine
that is not there.
