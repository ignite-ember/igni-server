"""Data models for the inference backend pool.

These describe **the customer's own inference nodes**, not a set of
upstream provider API keys. igni is self-hosted: a deployment serving 600
developers runs three GH200s, each with a vLLM instance, and this pool
spreads work across them.

That reframing changes one thing that matters more than the renames.
Provider quotas are throughput over time — "N requests per five hours" —
and a rolling window measures them correctly. A GPU node's constraint is
**concurrency right now**: how many sequences it is decoding this
instant. A rolling window would call a node full because it served a
thousand requests in the last hour, while it sits idle. So capacity here
is a gauge of in-flight leases, not a window of past requests, and every
acquire must be paired with a release.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.services.ai.node_drain import NodeDrain

# Which pool a backend serves. Interactive chat and batch indexing are
# separated so a 5,000-file indexing run cannot starve developers waiting
# on a completion — point them at different node sets and neither can
# monopolise the other's GPUs. On a single-node deployment a backend
# simply serves both.
PoolType = Literal['chat', 'codeindex']
VALID_POOL_TYPES: tuple[PoolType, ...] = ('chat', 'codeindex')


def pools_from_storage(raw: object) -> list[PoolType]:
    """Read the comma-joined ``inference_nodes.pools`` column.

    One parser. There were two: the registry filtered against
    `VALID_POOL_TYPES` and produced `list[PoolType]`, while
    `PinnedNodeView.of` kept whatever split out and typed it
    `list[str]` — so the admin view silently lost the validation the
    status view kept, and an unrecognised pool reached the portal as a
    pool.

    Unknown values are dropped rather than raising: this reads a column,
    and a row written by a future version that knows a third pool type
    must not make the node unreadable by this one.
    """
    return [pool for pool in str(raw or '').split(',') if pool in VALID_POOL_TYPES]  # type: ignore[misc]


# How long a lease may be held before the pool assumes its holder died.
# Generous, because a large summarisation request against a loaded node
# legitimately takes minutes — the cost of setting this too low is
# over-subscribing a node, which is worse than briefly under-using it.
DEFAULT_LEASE_TIMEOUT_SECONDS = 900


class InferenceBackend(BaseModel):
    """One inference endpoint the customer runs.

    ``models`` is a list because a vLLM node started with
    ``--enable-lora --lora-modules sql=... docs=...`` serves its base
    model *and* every adapter as separate ids on the same process, and a
    request picks between them with the ordinary ``model`` parameter.
    Modelling that as one string per node would mean either a node per
    adapter — three entries for one GPU, each with its own concurrency
    gauge counting the same sequences — or adapters being unreachable.

    They share weights, memory and the KV cache, so capacity and sticky
    routing stay per node. Only *selection* is per model.
    """

    id: str
    base_url: str

    # Written as ``models: [...]`` or, for the common single-model node,
    # ``model: "..."``. Both end up here; see the validator below.
    models: list[str] = Field(min_length=1)

    # Optional, and that is deliberate. A vLLM node on a private network
    # usually has no auth at all; requiring a token here would mean every
    # Helm chart carrying a hardcoded "none". Customers who front their
    # nodes with an auth proxy set it, and the pool passes it through.
    api_key: str | None = None

    # Concurrent requests this node will serve before the pool routes
    # elsewhere. The right value is roughly the vLLM instance's
    # ``--max-num-seqs``; going over does not fail, it just queues inside
    # the node where we can no longer see or balance it.
    max_concurrent: int = Field(default=64, gt=0)

    pools: list[PoolType] = ['chat']

    # Whether the model accepts image input. Only meaningful for chat —
    # the indexing pipeline is text-only — and surfaced so the chat
    # router can skip a text-only node when the request carries images.
    multimodal: bool = False

    @model_validator(mode='before')
    @classmethod
    def _accept_a_single_model(cls, data):
        """Let a one-model node be written ``model: "x"``.

        Not legacy tolerance — most nodes serve exactly one model, and
        making every config say ``models: ["x"]`` to express that would
        be noise. A payload giving both wins on ``models``.
        """
        if isinstance(data, dict) and 'models' not in data and 'model' in data:
            data = {**data, 'models': [data['model']]}
        return data

    @property
    def primary_model(self) -> str:
        """What a request that names no model gets.

        The first entry, which for a LoRA node is the base model — the
        adapters are opt-in by name.
        """
        return self.models[0]

    def serves(self, model: str | None) -> bool:
        """Whether this node can answer for ``model``.

        ``None`` means the caller did not care, and every node can answer
        that.
        """
        return model is None or model in self.models

    @model_validator(mode='after')
    def _base_url_is_absolute(self) -> 'InferenceBackend':
        if not self.base_url.startswith(('http://', 'https://')):
            raise ValueError(f'Backend {self.id!r}: base_url must start with http:// or https://')
        return self


class RegisteredNode(BaseModel):
    """A node that announced itself, and when we last heard from it.

    ``last_seen`` is kept even though Redis is already expiring the key,
    because the portal shows it: "registered, last heartbeat 40s ago" is
    the difference between a node an operator trusts and one they are
    about to go and look at.
    """

    backend: InferenceBackend
    registered_at: float
    last_seen: float


# Where a backend came from. The portal shows it because the three are
# operated differently, and an operator chasing a node needs to know
# which thing to go and look at:
#
# * ``static``     — the JSON config file. Changed by a redeploy.
# * ``registered`` — announced itself at runtime. Changed by whatever
#                    starts and stops the cluster, and expires when it
#                    stops checking in.
# * ``pinned``     — added by an admin in the portal. Changed here, and
#                    never expires; see ``InferenceNode`` for why.
BackendSource = Literal['static', 'registered', 'pinned']


class HealthState(StrEnum):
    """Whether a node is answering, in the three states worth acting on.

    ``UNKNOWN`` means we have not managed to look yet. It is a distinct
    value rather than an absent one so the portal can say so, but it is
    rendered the same amber as ``DEGRADED``: an operator asking "is my
    cluster up?" is not helped by a fourth colour meaning "no idea".
    """

    HEALTHY = 'healthy'
    DEGRADED = 'degraded'
    UNREACHABLE = 'unreachable'
    UNKNOWN = 'unknown'


class NodeHealth(BaseModel):
    """The result of asking a node whether it is there.

    ``detail`` is written for a person: "No response within 3s" and "The
    node rejected our credentials" send somebody to different places, and
    a bare state would send them to the same one.
    """

    state: HealthState
    detail: str | None = None
    latency_ms: float | None = None


class NodeMetrics(BaseModel):
    """What the node reports about itself, from its own ``/metrics``.

    Distinct from ``in_flight``, which counts leases *we* issued. That is
    exact for traffic igni routed and blind to everything else, so on a
    shared node the two disagree — and the node is right.

    Every field is optional because not every node is vLLM, and a
    deployment behind a proxy may not expose ``/metrics`` at all. Absent
    means "we could not ask", never "zero".
    """

    running: int | None = None
    waiting: int | None = None
    kv_cache_percent: float | None = None

    # hits / queries over the node's lifetime. Checks the pool's largest
    # design decision — sticky routing for KV-cache locality — which was
    # otherwise asserted and never measured.
    prefix_cache_hit_percent: float | None = None

    # Sequences discarded and redone because the node ran out of KV
    # blocks. Rising means thrashing, and users feel it as latency well
    # before anything errors.
    preemptions: int | None = None

    @property
    def reported(self) -> bool:
        """Whether the node told us anything at all."""
        return any(
            value is not None
            for value in (
                self.running,
                self.waiting,
                self.kv_cache_percent,
                self.prefix_cache_hit_percent,
                self.preemptions,
            )
        )


class ProbedNode(BaseModel):
    """What an address turned out to be, when asked directly.

    Used before a node exists as configuration: the admin pastes a URL,
    and this is what came back.
    """

    health: NodeHealth
    models: list[str] = []

    # The node answered 401/403. A separate flag rather than something
    # read out of ``detail``, because it is the single most likely
    # outcome of pasting an address — vLLM started with ``--api-key``
    # protects ``/v1``, and ``/v1/models`` is what we ask — and the
    # caller has a specific thing to say about it.
    needs_credentials: bool = False


class BackendStatus(BaseModel):
    """One backend as the portal sees it: what it is, and how busy.

    ``in_flight`` and ``max_concurrent`` are the pair that matters. A
    cluster where every node sits at its limit is a cluster that needs
    another node, and that is the decision this view exists to support.
    """

    id: str
    base_url: str
    models: list[str]
    pools: list[PoolType]
    multimodal: bool
    source: BackendSource
    in_flight: int
    max_concurrent: int
    health: NodeHealth

    # The node's own view, when it publishes one. Shown alongside
    # ``in_flight`` rather than replacing it: they measure different
    # things, and where they disagree that is itself worth seeing.
    metrics: NodeMetrics | None = None

    # Registered nodes only; None for static ones, which have no
    # heartbeat to be late on.
    last_seen_seconds_ago: float | None = None
    registered_at: float | None = None

    # Set when an operator has taken this node out of rotation. Carried
    # on the status rather than filtered out of it: a switch that makes
    # its subject disappear cannot be switched back.
    drain: NodeDrain | None = None

    @property
    def utilization(self) -> float:
        return self.in_flight / self.max_concurrent if self.max_concurrent else 0.0


class BackendLease(BaseModel):
    """A claim on one backend, held for the duration of one request.

    ``lease_id`` is what releases it. Holding this rather than a bare
    backend id is what makes the concurrency gauge correct: two
    concurrent requests to the same node hold two leases, and releasing
    one does not free the other.
    """

    lease_id: str
    backend_id: str
    base_url: str
    model: str
    api_key: str | None = None


class NoBackendAvailableError(Exception):
    """Every backend serving this pool is at its concurrency limit.

    Distinct from "no backend is configured" (a deployment problem) —
    this one is transient and the caller should retry rather than fail
    the run.
    """

    def __init__(self, pool: str) -> None:
        self.pool = pool
        super().__init__(f'Every inference backend for pool {pool!r} is at capacity')


class NoBackendConfiguredError(Exception):
    """Nothing serves this pool at all.

    A configuration error, not a load problem. Retrying will not help.
    """

    def __init__(self, pool: str) -> None:
        self.pool = pool
        super().__init__(f'No inference backend is configured for pool {pool!r}')


class AllBackendsDrainedError(Exception):
    """Everything serving this pool has been taken out of rotation.

    Distinct from both of the above, because the fix is neither "add a
    node" nor "wait": somebody switched these off from the portal and can
    switch them back. Saying "no backend is configured" to an
    administrator who drained the last one is how a two-second fix
    becomes a support ticket.
    """

    def __init__(self, pool: str, drained: list[str]) -> None:
        self.pool = pool
        self.drained = drained
        listed = ', '.join(sorted(drained))
        super().__init__(
            f'Every inference backend for pool {pool!r} has been drained ({listed}). '
            f'Resume one from the portal, under Inference.'
        )


class ModelNotAvailableError(Exception):
    """The requested model is not served by any backend in this pool."""

    def __init__(self, model: str, available: list[str]) -> None:
        self.model = model
        self.available = available
        listed = ', '.join(sorted(set(available))) if available else 'none'
        super().__init__(f'Model {model!r} is not served by this deployment. Available: {listed}')
