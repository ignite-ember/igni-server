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
import json
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.services.ai.node_drain import NodeDrain
from app.services.ai.providers.kinds import BackendKind, Wire

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

    # Who serves this backend. ``openai`` — the default, and every backend
    # there was before kinds — is an OpenAI-compatible URL with an optional
    # Bearer key. The cloud kinds are signed with the cloud's own
    # credentials; see ``app/services/ai/providers``.
    kind: BackendKind = BackendKind.OPENAI

    # Given for every kind but ``vertex_openai``, whose address is built
    # from ``project`` and ``region`` (see ``_vertex_has_an_address``).
    base_url: str

    # Vertex AI only: where the model runs, and in which project.
    region: str | None = None
    project: str | None = None

    # A cloud kind's stored credential, as JSON — a GCP service-account
    # key, or an Azure app's ``tenant_id``/``client_id``/``client_secret``.
    # Absent means the server's own identity (Workload Identity, Managed
    # Identity): the cloud's default chain, with nothing kept here.
    credential: str | None = Field(default=None, repr=False)

    # The name a caller asks for, mapped to the name the provider knows:
    # an Azure deployment, ``google/gemini-2.5-pro`` on Vertex. A model
    # absent here is sent as asked for. Callers, usage and stickiness only
    # ever see the names in ``models``.
    model_ids: dict[str, str] = Field(default_factory=dict)

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

    # Whether the model accepts image input, so the chat router can skip a
    # text-only node when the request carries images.
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

    @property
    def wire(self) -> Wire:
        """The API this backend speaks, and so the endpoint its models are served on."""
        return self.kind.wire

    @model_validator(mode='before')
    @classmethod
    def _vertex_has_an_address(cls, data):
        """Give a Vertex backend the address its project and region make.

        Vertex AI's OpenAI-compatible endpoint is the same path in every
        project, so an operator names the project and the region rather
        than pasting a URL. ``global`` has no regional prefix. A base URL
        given anyway wins: a private endpoint is still Vertex.
        """
        if isinstance(data, dict) and data.get('kind') == BackendKind.VERTEX_OPENAI and not data.get('base_url'):
            project, region = data.get('project'), data.get('region')
            if not (project and region):
                where = f'Backend {data.get("id")!r} (vertex_openai)'
                raise ValueError(f'{where}: a Vertex backend needs its project and region')
            host = (
                'https://aiplatform.googleapis.com'
                if region == 'global'
                else f'https://{region}-aiplatform.googleapis.com'
            )
            data = {**data, 'base_url': f'{host}/v1/projects/{project}/locations/{region}/endpoints/openapi'}
        return data

    @model_validator(mode='after')
    def _base_url_is_absolute(self) -> 'InferenceBackend':
        if not self.base_url.startswith(('http://', 'https://')):
            raise ValueError(f'Backend {self.id!r}: base_url must start with http:// or https://')
        return self

    @model_validator(mode='after')
    def _fields_fit_the_kind(self) -> 'InferenceBackend':
        """Refuse a field the kind would silently ignore.

        A Vertex backend given an ``api_key`` would send Google's token and
        never the key, and an operator reading the configuration would
        believe the key was in use. Saying so at load time is cheaper than
        that afternoon.
        """
        where = f'Backend {self.id!r} ({self.kind.value})'
        vertex = self.kind is BackendKind.VERTEX_OPENAI
        if vertex and not (self.project and self.region):
            raise ValueError(f'{where}: a Vertex backend needs its project and region')
        if not vertex and (self.project or self.region):
            raise ValueError(f'{where}: project and region are for a Vertex backend only')
        if vertex and self.api_key:
            raise ValueError(f'{where}: Vertex is signed with Google credentials, not an api_key')
        if not self.kind.is_cloud and self.credential:
            raise ValueError(f'{where}: a stored credential is for a cloud backend; give an api_key instead')
        if self.kind is BackendKind.AZURE_OPENAI and self.api_key and self.credential:
            raise ValueError(f'{where}: give the resource key or a client secret, not both')
        if self.credential:
            _check_credential(where, self.kind, self.credential)
        unknown = sorted(set(self.model_ids) - set(self.models))
        if unknown:
            raise ValueError(f'{where}: model_ids names models it does not serve: {", ".join(unknown)}')
        return self


def _check_credential(where: str, kind: BackendKind, credential: str) -> None:
    """A stored credential is the JSON its cloud's SDK takes. Never echoes it."""
    try:
        stored = json.loads(credential)
    except ValueError:
        raise ValueError(f'{where}: the stored credential is not JSON') from None
    if not isinstance(stored, dict):
        raise ValueError(f'{where}: the stored credential is not a JSON object')
    if kind is BackendKind.VERTEX_OPENAI and stored.get('type') != 'service_account':
        raise ValueError(f'{where}: the stored credential is not a Google service-account key')
    if kind is BackendKind.AZURE_OPENAI:
        missing = [k for k in ('tenant_id', 'client_id', 'client_secret') if not stored.get(k)]
        if missing:
            raise ValueError(f'{where}: the stored credential lacks {", ".join(missing)}')


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
    kind: BackendKind = BackendKind.OPENAI
    credential: str | None = Field(default=None, repr=False)
    model_ids: dict[str, str] = Field(default_factory=dict)

    def upstream_model(self, model: str) -> str:
        """The name the provider knows *model* by."""
        return self.model_ids.get(model, model)

    @property
    def completions_url(self) -> str:
        """The node's OpenAI-compatible chat completions endpoint."""
        return f'{self.base_url.rstrip("/")}/chat/completions'

    def auth_headers(self) -> dict[str, str]:
        """Headers for a call to this node.

        No ``Authorization`` when the node has no key: a vLLM on a private
        network has no auth, and ``Bearer None`` would be a confusing 401.
        """
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        return headers


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
