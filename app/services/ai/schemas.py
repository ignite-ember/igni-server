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

from pydantic import BaseModel

from app.services.ai.backend import InferenceBackend
from app.services.ai.node_drain import NodeDrain
from app.services.ai.providers.kinds import BackendKind

# How long a lease may be held before the pool assumes its holder died.
# Generous, because a large summarisation request against a loaded node
# legitimately takes minutes — the cost of setting this too low is
# over-subscribing a node, which is worse than briefly under-using it.
DEFAULT_LEASE_TIMEOUT_SECONDS = 900


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
    multimodal: bool
    source: BackendSource
    kind: BackendKind = BackendKind.OPENAI
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


class NoBackendAvailableError(Exception):
    """Every backend is at its concurrency limit.

    Distinct from nothing being configured (the provider's
    ``BackendConfigError``, a deployment problem) — this one is transient and the caller should retry rather than fail
    the run.
    """

    def __init__(self) -> None:
        super().__init__('Every inference backend is at capacity')


class AllBackendsDrainedError(Exception):
    """Every backend has been taken out of rotation.

    Distinct from both nothing being configured and every node being
    busy, because the fix is neither "add a node" nor "wait": somebody switched these off from the portal and can
    switch them back. Saying "no backend is configured" to an
    administrator who drained the last one is how a two-second fix
    becomes a support ticket.
    """

    def __init__(self, drained: list[str]) -> None:
        self.drained = drained
        listed = ', '.join(sorted(drained))
        super().__init__(
            f'Every inference backend has been drained ({listed}). Resume one from the portal, under Inference.'
        )


class ModelNotAvailableError(Exception):
    """The requested model is not served by any backend in this pool."""

    def __init__(self, model: str, available: list[str]) -> None:
        self.model = model
        self.available = available
        listed = ', '.join(sorted(set(available))) if available else 'none'
        super().__init__(f'Model {model!r} is not served by this deployment. Available: {listed}')
