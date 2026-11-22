The server does not host a model. It keeps a list of inference endpoints you run — vLLM instances, or anything else that speaks the OpenAI API — and leases one node per request. If you own the GPUs, this is the page that explains how they get into that list, how a request picks between them, and which defaults will bite.

## One list of nodes

igni server routes chat over your nodes and nothing else. Every node in the list serves chat: there is one list, and no way to split it.

0.7.0 removed the two-pool model. A node then declared which pools it served — `chat` for interactive completions, `codeindex` for batch indexing — and pointing the two at different nodes was what stopped a batch indexing run from starving the developers waiting on a completion. Nothing in igni server ever took a `codeindex` lease, because CodeIndex runs its own nodes from its own configuration; a node carrying only `codeindex` was registered here and used by nothing this server does. A node no longer has `pools` at all: not in registration, not on the portal's node page, not in storage, where migration 0029 drops the column. A `pools` key left in a configuration file written for an earlier release is ignored rather than refused, so the file still loads.

One list of nodes serves every organisation on the install, so a node one organisation's admin added would receive the others' prompts, and a node they drained would push the others' traffic elsewhere. Adding, changing, probing, draining or removing a node is therefore a super admin's decision, not one organisation's.

## Describing a node

A node is a JSON object with these fields:

| Field | Default | Notes |
|---|---|---|
| `id` | required | Namespaces the concurrency gauge, so it must be unique across every source. Nodes registered through the API or added in the portal are additionally constrained to `^[A-Za-z0-9._-]+$`, max 128 chars. |
| `base_url` | required | Must start with `http://` or `https://`. Rejected at the door otherwise. |
| `models` | required | Every id the node answers to. `model: "x"` is accepted for a single-model node; when both are given, `models` wins. |
| `api_key` | `null` | Only if the node is behind an auth proxy. Sent as `Authorization: Bearer`. |
| `max_concurrent` | `64` | Requests in flight before the server routes elsewhere. Roughly the vLLM instance's `--max-num-seqs`. |
| `multimodal` | `false` | Recorded and displayed. See the limits below. |

```json
[
  {
    "id": "gpu-fixed-01",
    "base_url": "http://gpu-fixed-01.internal:8000/v1",
    "model": "igni-1.0-4b",
    "max_concurrent": 64
  },
  {
    "id": "gpu-lora-01",
    "base_url": "http://gpu-lora-01.internal:8000/v1",
    "models": ["igni-1.0-4b", "sql", "docs"],
    "max_concurrent": 32
  }
]
```

The second node is a vLLM process started with LoRA adapters: one GPU, one KV cache, three selectable model ids. Capacity and sticky routing stay per node; only selection is per model.

That array reaches the server by one of two settings, or neither:

| Setting | Default | What it does |
|---|---|---|
| `inference_backends_path` | `''` | Path to the JSON file (a ConfigMap or bind mount). Wins when both are set. A path that does not exist raises rather than being ignored — startup logs it as a warning and carries on, and every request that needs the backend list fails afterwards. |
| `inference_backends` | `''` | The same JSON inline, for compose and single-node installs. |
| `node_registration_token` | `''` | Shared secret GPU nodes present to register themselves. Empty disables the registration API entirely. |

Settings are read from the environment case-insensitively (`INFERENCE_BACKENDS_PATH`), with `__` nesting — `AI__UPSTREAM_TIMEOUT` sets `ai.upstream_timeout`.

Nodes can also come from two other places: an admin adding one by hand in the portal (stored in Postgres, never expires), and nodes that register themselves. Precedence runs **static, then pinned, then registered** — the further from the deployment's own configuration, the less a node gets to override. The merged list is re-read every 30 seconds, so adding a GPU does not need a restart.

## Cloud model providers

A node does not have to be a GPU you run. igni server also speaks to the managed Claude and OpenAI-compatible endpoints of the three clouds, as nodes in the same list, routed and load-balanced the same way. Which provider a node is, is its **`kind`**:

| `kind` | What it reaches | Wire |
|---|---|---|
| `openai` *(default)* | a vLLM node, or anything else that speaks the OpenAI API | `POST /v1/chat/completions` |
| `azure_openai` | Azure OpenAI's v1 API | `POST /v1/chat/completions` |
| `vertex_openai` | Vertex AI's OpenAI-compatible endpoint (Gemini, open models) | `POST /v1/chat/completions` |
| `anthropic` | Anthropic's own API, or an Anthropic-compatible gateway | `POST /v1/messages` |
| `bedrock` | Claude on AWS Bedrock | `POST /v1/messages` |
| `vertex_anthropic` | Claude on Vertex AI | `POST /v1/messages` |
| `azure_anthropic` | Claude in Microsoft Foundry | `POST /v1/messages` |

Leave `kind` out and a node is `openai` — so the GPU nodes above need nothing new. A cloud node takes the same `id`, `models`, `max_concurrent` and `multimodal` fields, plus a few of its own:

| Field | For | Notes |
|---|---|---|
| `kind` | all | One of the seven above. Defaults to `openai`. |
| `base_url` | all | Required for `openai` and `azure_openai`. For the others it is **derived** from `kind`, `region` and `project` when left empty (see below); set it explicitly only to reach a private endpoint. |
| `region` | `bedrock`, `vertex_openai`, `vertex_anthropic` | Required for those three; rejected on any other kind. |
| `project` | `vertex_openai`, `vertex_anthropic` | The Google Cloud project. Required for Vertex; rejected on any other kind. |
| `model_ids` | all | A map from the id the caller asks for to the provider's own name — an Azure deployment name, or `google/gemini-2.5-pro`. Every key must also appear in `models`. |
| `auth` | all | How the server authenticates (see below). Omit it to take the kind's default. |
| `auth_settings` | all | The non-secret settings the chosen method needs — a client id, a tenant, a service account, a role ARN. |
| `credential` | some methods | The method's secret (a client secret, a service-account key, an AWS secret access key). Stored encrypted and never returned. |
| `api_key` | some methods | A static bearer key, or Azure's resource key. Stored encrypted and never returned. |

When `base_url` is empty, the server builds it from the kind:

- `vertex_openai` / `vertex_anthropic` → `https://{region}-aiplatform.googleapis.com/...` for the project and region (the host drops the region prefix when `region` is `global`);
- `bedrock` → `https://bedrock-runtime.{region}.amazonaws.com`;
- `anthropic` → `https://api.anthropic.com`.

### Ambient credentials, or static ones

Each cloud kind can authenticate either with a **static** secret you give igni server, or with the server's **own cloud identity** — its instance role, workload identity or managed identity — so no key is stored at all. That is the `auth` field; omit it and the server picks the kind's default, which is `ambient` (the server's own identity) for every cloud kind, and `none` for a plain `openai` node.

| `auth` | Cloud | What it uses | Needs |
|---|---|---|---|
| `none` | `openai` | no Authorization header | — |
| `api_key` | `openai`, `azure_openai`, `azure_anthropic`, `anthropic` | a static key (Azure's own `api-key` header for `azure_openai`, otherwise `Authorization: Bearer`) | `api_key` |
| `ambient` | Azure, Vertex, Bedrock | the server's own cloud identity — Azure `DefaultAzureCredential`, GCP Application Default Credentials, the AWS default credential chain | — |
| `managed_identity` | `azure_openai`, `azure_anthropic` | a user-assigned Azure managed identity | `auth_settings.client_id` |
| `client_secret` | `azure_openai`, `azure_anthropic` | an app registration | `auth_settings.tenant_id`, `auth_settings.client_id`, `credential` (the client secret) |
| `impersonate` | `vertex_openai`, `vertex_anthropic` | the server identity impersonating a service account | `auth_settings.service_account` |
| `service_account_key` | `vertex_openai`, `vertex_anthropic` | a service-account JSON key | `credential` (the JSON key) |
| `assume_role` | `bedrock` | the server identity assumes an IAM role (STS) | `auth_settings.role_arn` (and optionally `external_id`) |
| `access_keys` | `bedrock` | a static IAM user | `auth_settings.access_key_id`, `credential` (the secret access key) |

`anthropic` offers only `api_key`; the other cloud kinds default to `ambient` and offer the static methods beside it. The ambient path means igni server reaches the model with the identity the machine already runs as — an instance profile or IRSA role on AWS, workload identity or an attached service account on GCP, a system managed identity on Azure — and there is nothing to rotate or leak. Bedrock requests are signed with SigV4 (not a bearer token) whichever AWS method is used; token-based methods are renewed by the server as they expire.

A node whose fields do not fit its kind is refused at load with the reason — a Vertex node with no project, a `region` on an `anthropic` node, a method the kind does not offer, a missing setting, an `api_key` where a `credential` was wanted. A broken static file fails every request; the portal and node-registration paths refuse the one node and say why.

### Examples

A GPU node, an Azure OpenAI deployment, Claude on Bedrock with the server's own role, and Gemini on Vertex, in one list:

```json
[
  {
    "id": "gpu-fixed-01",
    "base_url": "http://gpu-fixed-01.internal:8000/v1",
    "model": "igni-1.0-4b",
    "max_concurrent": 64
  },
  {
    "id": "azure-eu",
    "kind": "azure_openai",
    "base_url": "https://acme.openai.azure.com/openai/v1",
    "models": ["gpt-5"],
    "model_ids": {"gpt-5": "gpt-5-deployment"}
  },
  {
    "id": "bedrock-eu",
    "kind": "bedrock",
    "region": "eu-central-1",
    "models": ["claude-opus-5-5"],
    "auth": "ambient"
  },
  {
    "id": "vertex-eu",
    "kind": "vertex_openai",
    "project": "acme-ai",
    "region": "europe-west4",
    "models": ["gemini-pro"],
    "model_ids": {"gemini-pro": "google/gemini-2.5-pro"}
  }
]
```

Static secrets go in the same object. An Azure app registration:

```json
{
  "id": "azure-eu",
  "kind": "azure_openai",
  "base_url": "https://acme.openai.azure.com/openai/v1",
  "models": ["gpt-5"],
  "auth": "client_secret",
  "auth_settings": {"tenant_id": "...", "client_id": "..."},
  "credential": "<the client secret>"
}
```

A cloud node can come from any of the three sources below — the static file, the inline setting, or the portal (*Deployment → Inference*, where the secret is entered once and comes back only as "set"). The same precedence — static, then pinned, then registered — applies.

## Letting a cluster register itself

Editing a ConfigMap and waiting for a rollout is the wrong workflow for hardware you start on Tuesday and stop on Wednesday. Set `node_registration_token` and three verbs appear, authenticated with `X-Igni-Node-Token`:

```bash
curl -sS -X POST https://igni.internal/v1/nodes \
  -H "X-Igni-Node-Token: $IGNI_NODE_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"id":"gpu-burst-01",
       "base_url":"http://gpu-burst-01.internal:8000/v1",
       "model":"igni-1.0-4b",
       "max_concurrent":64}'
# {"id":"gpu-burst-01","registered_at":...,"heartbeat_interval_seconds":60}

curl -sS -X POST .../v1/nodes/gpu-burst-01/heartbeat -H "X-Igni-Node-Token: $IGNI_NODE_TOKEN"
curl -sS -X DELETE .../v1/nodes/gpu-burst-01       -H "X-Igni-Node-Token: $IGNI_NODE_TOKEN"
```

A registration survives **180 seconds** without a heartbeat, and the response tells you the interval to use (60 seconds) rather than making you hardcode it. That expiry is the point: a spot reclaim or a kernel panic never gets to run the delete, and a node that stopped answering drops out of rotation on its own. Registering is idempotent — re-registering an id replaces it, which is how a rescheduled node moves address. A heartbeat for an expired id answers **404**, meaning "register again with your full description". Claiming an id that the static config names is **409**, where the automation that got it wrong can see it. A delete is idempotent too: **204** whether or not the node was there.

Unset the token and these routes answer 404 rather than advertising a disabled endpoint; a wrong token is 401. Set it to fewer than 32 characters and startup warns: it is compared without any attempt limit, so its length is the whole of its strength.

## How a request picks a node

Per `acquire`, in order: drop the drained nodes, keep those serving the requested model (membership, so a LoRA adapter counts), then

1. **Sticky.** The caller is pinned to their last node for **6 hours**, keyed by model and user. A vLLM node keeps a prefix/KV cache and the next turn shares almost all of its prefix; landing elsewhere re-decodes the whole context. If the pinned node is full, the pin is skipped rather than queued behind.
2. **Least loaded by ratio.** `in_flight / max_concurrent`, so an 8-slot node with 4 in flight is treated as exactly as loaded as a 64-slot node with 32. A node at 1.0 is never chosen.
3. **Lease.** In-flight work is a live gauge in Redis, not a rolling window, and leases older than the 900-second timeout are pruned on read so a SIGKILLed worker does not walk a node out of rotation permanently.

If the chat request named no model, the model pinned by the caller's group is used; if the group pins none either, the node's first model id is.

When the first node fails, the request is attempted at most **3 times** — the first try plus two retries — with a `0.25s × attempt` pause, excluding the node that just failed; the failed node's slot is handed back first. On a deployment where the failed node is the only candidate, the retry falls back to it rather than failing outright. A 4xx that is not 408 or 429 is *not* retried — the request does not change between nodes.

| Condition | Answer |
|---|---|
| Requested model served by nobody | `404`, listing what is available |
| Nothing configured at all | `503` — a deployment problem, retrying will not help |
| Every node at its concurrency limit | The request **waits its turn** in a queue, shared fairly between organisations and then between people and apps. After `CHAT_QUEUE_MAX_WAIT_SECONDS` (900) it gets `429` with `Retry-After: 30`; with `CHAT_QUEUE_MAX_LENGTH` (2000) already waiting in that replica, it gets the 429 at once |
| Node answered 4xx | the node's own status, passed through — except `429`, which becomes `429` with `Retry-After: 30` |
| Node answered 5xx, or a 200 with an unreadable body | `502` |
| Node went silent partway through | `504` |

Non-streaming calls use `ai.upstream_timeout` (default **300.0** seconds). Streaming uses a fixed connect 10s / read 120s / write 10s / pool 10s — not configurable.

## Watching your nodes, and taking one out

`GET /v1/portal/inference` (org admins only — base URLs describe a private network) answers one flat list of nodes with one set of totals: the in-flight count, the capacity and the utilisation over every node that can take work, plus each node's own load and health. A drained node keeps its row but is left out of the totals, so a machine that is out of rotation does not flatter the numbers. Health comes from `GET {base_url}/models` with a 3-second timeout, cached 15 seconds, deliberately not a completion. A node that answers is still **degraded** if it rejects the credentials, does not serve the models it is configured for, has stopped heartbeating for 180 seconds, or is at its limit.

The server also scrapes the node's own `/metrics` — at the server root, with a trailing `/v1` stripped — for `vllm:num_requests_running`, `vllm:num_requests_waiting`, `vllm:kv_cache_usage_perc` (or the older `gpu_cache_usage_perc`), prefix-cache hits and preemptions. `waiting` is the one to watch: anything queued inside the node means `max_concurrent` is set too high for that hardware, and it downgrades the node to degraded.

Changing the node set is a super admin's job. `POST /v1/portal/inference/nodes/{id}/drain` works for a node from any source, stops it being selected and being advertised, and leaves in-flight requests to finish on it. Watch its `in_flight` fall to zero, then work on it. A drain has no expiry and survives the node re-registering — only `DELETE .../drain` clears it. Draining an id nothing serves is refused with a `404` rather than quietly applying to nothing, and adding, editing and removing a node are refused with a `403` to anybody who is not a super admin.

## How the agent reaches a model

The agent never talks to a GPU. On start it calls `GET /v1/chat/models`, which returns model ids and deliberately no upstream URL, and registers each one locally pointing at `{api_url}/v1` with the api key set to the sentinel `cloud_token`, swapped for the access token the agent already holds at call time. Entries you wrote yourself are never overwritten; if no default is set, the first entry in the local registry becomes it.

`api_url` defaults to empty, and discovery is skipped quietly until you set it in `~/.igni/config.yaml` — and again when no access token is stored, since the call is made with one. The fetch has a 3-second timeout and every failure is soft: the agent boots on whatever is already in local config.

On the CLI, `--model` chooses, `-m/--message` sends one message and `-p/--pipe` reads stdin. There is no terminal chat UI; interactive use is the desktop app. How a model is chosen when nobody names one is in [Models and the Factory](/docs/core/models-and-the-factory).

## Limits worth knowing

- **`max_concurrent` is advisory.** Going over does not fail — work queues *inside* the node, where the server can neither see nor balance it. The node's `waiting` gauge is the only thing that reveals it.
- **A leaked lease costs 900 seconds.** Until the staleness sweep prunes it, that node looks one busier than it is.
- **`multimodal` does not affect routing.** It is accepted at registration, stored, and shown in the portal, but selection filters only on drain state and model id. A request carrying images can still land on a text-only node.
- **Draining every node is not handled gracefully by chat.** The server raises a distinct "all drained" error with a sentence telling you to resume one from the portal, and the chat endpoint does not catch it — the caller gets a generic 500 instead of that sentence.
- **Redis is load-bearing.** Leases, sticky pins, drains, registrations and the health cache all live there. A Redis restart empties the registry; live nodes re-announce within one heartbeat and static nodes are unaffected. Drain lookups deliberately fail *open*, so during a Redis outage a drained node may briefly take traffic again.
- **A broken static file fails every request; a broken registry does not.** Malformed JSON or a duplicate id raises at load, while an unreachable registry degrades to the static and pinned nodes.

## Where to go next

- **[Administering your organisation](/docs/server/administering-your-organisation)** — with models serving, the next step is admitting the people who will use them.
- **[Server configuration reference](/docs/server/server-configuration)** — the backend, timeout and queue settings behind this page.
- **[Models and the Factory](/docs/core/models-and-the-factory)** — igni's own models, and serving LoRA adapters.
