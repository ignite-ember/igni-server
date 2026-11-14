"""Request and response shapes for node registration and the node view.

Separate from ``app.services.ai.schemas`` on purpose. ``InferenceBackend``
carries ``api_key``, and a model that is both the wire format of a public
endpoint and the thing the portal serialises is one careless
``response_model`` away from publishing it. The registration request
accepts a key and never gives one back; the portal view has no field for
one at all.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, ValidationError

from app.db.sql.models.inference_node import InferenceNode
from app.db.sql.schemas.inference_node import NodeChanges
from app.services.ai.fleet_schemas import FleetSnapshot
from app.services.ai.providers.auth import AuthMethod, default_method, METHODS
from app.services.ai.providers.kinds import BackendKind
from app.services.ai.schemas import BackendStatus, InferenceBackend


class NodeRegistration(BaseModel):
    """What a starting node tells us about itself."""

    id: str = Field(
        min_length=1,
        max_length=128,
        # Ids namespace Redis keys and appear in the portal, so the
        # characters are constrained rather than trusted — a colon would
        # let a caller reach outside its own key.
        pattern=r'^[A-Za-z0-9._-]+$',
        description='Stable identifier for this node; re-registering the same id replaces the entry',
    )
    base_url: str = Field(description='Where the OpenAI-compatible API is served, e.g. http://gpu-3:8000/v1')
    # Every id this node answers to. A vLLM process started with
    # ``--enable-lora`` serves its base model and each adapter as
    # separate ids, and any of them may be requested.
    models: list[str] = Field(min_length=1, description='Model ids this node serves, base plus any LoRA adapters')
    api_key: str | None = Field(default=None, description='Only if the node is behind an auth proxy')
    max_concurrent: int = Field(default=64, gt=0, description="Roughly the vLLM instance's --max-num-seqs")
    multimodal: bool = Field(default=False, description='Whether the model accepts image input')

    @model_validator(mode='before')
    @classmethod
    def _accept_a_single_model(cls, data: object) -> object:
        """``model: "x"`` is accepted for a node serving just one.

        **Not a back-compatibility reader, and not removable.**
        `model` is the *documented* field: `docs/gpu-clusters.md` uses
        it in the registration example an operator copies and lists it
        in the field table, and `models` appears there nowhere. The
        plural arrived with LoRA adapters, where one vLLM process
        serves its base model and each adapter under separate ids.

        Said at length because a review read this as a legacy shim for
        a payload we control and proposed deleting it, which would
        break every registration in the published guide.
        """
        if isinstance(data, dict) and 'models' not in data and 'model' in data:
            data = {**data, 'models': [data['model']]}
        return data

    @model_validator(mode='after')
    def _base_url_is_absolute(self) -> 'NodeRegistration':
        """Checked here as well as on ``InferenceBackend``.

        Not redundant: validating only in ``to_backend`` would raise
        inside the handler, and the caller would get a 500 for what is
        plainly a bad request. A node registered with a relative URL is
        stored happily and then fails every request routed to it, so this
        has to be a 422 at the door.
        """
        if not self.base_url.startswith(('http://', 'https://')):
            raise ValueError('base_url must start with http:// or https://')
        return self

    def to_backend(self) -> InferenceBackend:
        """The routing model."""
        return InferenceBackend(
            id=self.id,
            base_url=self.base_url,
            models=list(self.models),
            api_key=self.api_key,
            max_concurrent=self.max_concurrent,
            multimodal=self.multimodal,
        )


class NodeRegistrationAccepted(BaseModel):
    """Confirmation, and how often to check back in.

    The interval is returned rather than documented so the customer's
    heartbeat loop reads it from the server it is talking to, instead of
    hardcoding a number that a later release might change underneath it.
    """

    id: str
    registered_at: float
    heartbeat_interval_seconds: int


class NodeHealthView(BaseModel):
    """Whether a node is answering, and why not when it is not.

    ``detail`` is written for a person. "No response within 3s" and "The
    node rejected our credentials" send somebody to different places, and
    a bare state would send them to the same one.
    """

    state: str
    detail: str | None = None
    latency_ms: float | None = None


class PinnedNodeCreate(BaseModel):
    """A node an admin is adding by hand: a GPU node, or a cloud model provider."""

    # Never echo a value in a 422: it may be a key or a credential.
    model_config = ConfigDict(hide_input_in_errors=True)

    node_id: str = Field(
        min_length=1,
        max_length=128,
        # Ids namespace Redis keys, so the characters are constrained
        # rather than trusted — a colon would let one node reach outside
        # its own gauge.
        pattern=r'^[A-Za-z0-9._-]+$',
        description='Stable identifier, unique across every source',
    )
    kind: BackendKind = Field(
        default=BackendKind.OPENAI,
        description='openai (a GPU node or any OpenAI-compatible API), azure_openai, or vertex_openai',
    )
    base_url: str | None = Field(
        default=None,
        description=(
            "Where the API is served, e.g. http://gpu-3:8000/v1, or an Azure resource's .../openai/v1. "
            'Built from project and region for vertex_openai.'
        ),
    )
    # Usually discovered rather than typed: POST /inference/nodes/discover
    # asks the node what it serves, which is the only way to get LoRA
    # adapter names right.
    models: list[str] = Field(min_length=1, description='Model ids to route here, base plus any LoRA adapters')
    api_key: str | None = Field(
        default=None, description="Only if the node is behind an auth proxy; an Azure resource's key"
    )
    max_concurrent: int = Field(default=64, gt=0, description="Roughly the vLLM instance's --max-num-seqs")
    multimodal: bool = False
    region: str | None = Field(default=None, description='vertex_openai: where the model runs, e.g. europe-west4')
    project: str | None = Field(default=None, description='vertex_openai: the Google Cloud project')
    auth: AuthMethod | None = Field(
        default=None,
        description='How requests are authenticated; GET /inference/auth-methods lists what each kind offers',
    )
    auth_settings: dict[str, str] = Field(
        default_factory=dict,
        description="The method's settings, not secret: an Azure client_id and tenant_id, a service_account",
    )
    credential: str | None = Field(
        default=None,
        description="The method's secret when it has one: an Azure client secret, a Google service-account key (JSON)",
    )
    model_ids: dict[str, str] = Field(
        default_factory=dict, description="The provider's own name for a model, e.g. an Azure deployment"
    )

    def backend(self) -> InferenceBackend:
        """The node as the router will see it. Raises `ValueError` naming what does not fit."""
        return backend_or_value_error(
            {
                'id': self.node_id,
                'kind': self.kind,
                'base_url': self.base_url,
                'models': self.models,
                'api_key': self.api_key or None,
                'max_concurrent': self.max_concurrent,
                'multimodal': self.multimodal,
                'region': self.region,
                'project': self.project,
                'credential': self.credential or None,
                'model_ids': self.model_ids,
                'auth': self.auth,
                'auth_settings': self.auth_settings,
            }
        )


def backend_or_value_error(fields: dict) -> InferenceBackend:
    """An `InferenceBackend`, with its validation failure as one plain `ValueError`.

    A `ValidationError` raised inside another model's validator is not
    reported as the field error it is; its messages are.
    """
    if not fields.get('base_url'):
        fields = {key: value for key, value in fields.items() if key != 'base_url'}
    try:
        return InferenceBackend.model_validate(fields)
    except ValidationError as exc:
        messages = [error['msg'].removeprefix('Value error, ') for error in exc.errors()]
        raise ValueError('; '.join(messages)) from None


class PinnedNodeUpdate(BaseModel):
    """A partial edit. Everything optional; absent means unchanged."""

    # Never echo a value in a 422: it may be a key or a credential.
    model_config = ConfigDict(hide_input_in_errors=True)

    base_url: str | None = None
    models: list[str] | None = Field(default=None, min_length=1)
    # Absent leaves the stored credential alone; an explicit empty string
    # clears it. The portal never sends the key back, so treating absent
    # as "clear" would wipe it on every edit of anything else.
    api_key: str | None = None
    max_concurrent: int | None = Field(default=None, gt=0)
    multimodal: bool | None = None
    enabled: bool | None = None
    region: str | None = None
    project: str | None = None
    # As ``api_key``: absent leaves the stored credential alone, an empty
    # string clears it.
    credential: str | None = None
    model_ids: dict[str, str] | None = None
    # A new method drops the secret and the settings the old one used,
    # unless this edit gives them.
    auth: AuthMethod | None = None
    auth_settings: dict[str, str] | None = None

    @field_validator('base_url')
    @classmethod
    def _base_url_is_absolute(cls, base_url: str | None) -> str | None:
        # A field validator, so a 422 carries this field's value and not the
        # whole body, whose api_key or credential it would echo.
        if base_url is not None and not base_url.startswith(('http://', 'https://')):
            raise ValueError('base_url must start with http:// or https://')
        return base_url

    def changes(self) -> NodeChanges:
        """The edit, as the persistence layer's own contract.

        Was `-> dict`, unannotated, with a fully knowable key set — the
        model's own fields — which the caller then splatted back into
        `NodeChanges(**body.changes())`.

        `exclude_unset` is the whole semantics and survives: absent
        means unchanged, and an explicitly-null `api_key` means clear
        it. Dumping everything would turn every edit into a write of
        every field.
        """
        return NodeChanges(**self.model_dump(exclude_unset=True))


class PinnedNodeView(BaseModel):
    """A node added here, as the portal gets it back.

    No ``api_key``: it goes in and is never returned. An admin who needs
    to change it sets a new one.
    """

    node_id: str
    base_url: str
    models: list[str]
    max_concurrent: int
    multimodal: bool
    enabled: bool
    has_api_key: bool
    kind: BackendKind = BackendKind.OPENAI
    region: str | None = None
    project: str | None = None
    model_ids: dict[str, str] = Field(default_factory=dict)
    auth: AuthMethod = AuthMethod.NONE
    auth_settings: dict[str, str] = Field(default_factory=dict)
    # As ``has_api_key``: whether one is stored, never what it is.
    has_credential: bool = False

    @classmethod
    def of(cls, node: InferenceNode) -> 'PinnedNodeView':
        """Project a stored node for the portal."""
        return cls(
            node_id=str(node.node_id),
            base_url=str(node.base_url),
            models=list(node.models or []),
            max_concurrent=int(node.max_concurrent),
            multimodal=bool(node.multimodal),
            enabled=bool(node.enabled),
            has_api_key=bool(node.api_key),
            kind=BackendKind(node.kind or 'openai'),
            region=node.region,
            project=node.project,
            model_ids=dict(node.model_ids or {}),
            auth=(
                AuthMethod(node.auth)
                if node.auth
                else default_method(
                    BackendKind(node.kind or 'openai'),
                    has_api_key=bool(node.api_key),
                    has_credential=bool(node.credential),
                )
            ),
            auth_settings=dict(node.auth_settings or {}),
            has_credential=bool(node.credential),
        )


class AuthMethodView(BaseModel):
    """One way a kind of backend can authenticate, for the portal to build its form from."""

    method: AuthMethod
    #: Settings the method needs, not secret.
    settings: list[str]
    #: Settings it takes but does not need: a role's external ID.
    optional: list[str] = Field(default_factory=list)
    #: Where its secret goes, `api_key` or `credential`, or none.
    secret: str | None


class BackendKindView(BaseModel):
    """A kind of backend and the methods it offers, the first being its default."""

    kind: BackendKind
    wire: str
    methods: list[AuthMethodView]

    @classmethod
    def every(cls) -> list['BackendKindView']:
        """Every kind, from the server's own table."""
        return [
            cls(
                kind=kind,
                wire=kind.wire.value,
                methods=[
                    AuthMethodView(
                        method=method,
                        settings=list(spec.settings),
                        optional=list(spec.optional),
                        secret=spec.secret,
                    )
                    for method, spec in methods.items()
                ],
            )
            for kind, methods in METHODS.items()
        ]


class NodeMetricsView(BaseModel):
    """The node's own numbers, when it publishes them.

    ``running`` will differ from ``in_flight`` on a node igni does not
    have to itself — somebody calling it directly, or another service
    sharing the GPU — and where they disagree the node is right.
    """

    running: int | None = None
    waiting: int | None = None
    kv_cache_percent: float | None = None
    prefix_cache_hit_percent: float | None = None
    preemptions: int | None = None


class NodeDrainRequest(BaseModel):
    """Optionally, why.

    Optional because requiring a reason during an incident is friction at
    the worst moment, and a required field people fill with "." is worse
    than an empty one. Bounded so it cannot become a place to store
    something.
    """

    reason: str | None = Field(default=None, max_length=280)


class NodeDrainView(BaseModel):
    """Why a node is not serving, and on whose say-so.

    Carried with the state rather than left to the audit trail: a drained
    node found weeks later is the question "why is this off?", and
    searching an audit log is a poor way to answer something the record
    can say itself.
    """

    by: str
    by_email: str | None = None
    at: datetime
    reason: str | None = None


class NodeStatusView(BaseModel):
    """One node, as the portal shows it.

    ``utilization`` is computed here rather than in the browser so every
    consumer agrees on what it means, and because it is the number an
    operator actually reads — "in flight over capacity" is the same
    information but has to be divided in your head.
    """

    id: str
    base_url: str
    models: list[str]
    multimodal: bool
    source: str
    kind: BackendKind = BackendKind.OPENAI
    in_flight: int
    max_concurrent: int
    utilization: float
    health: NodeHealthView
    metrics: NodeMetricsView | None = None
    last_seen_seconds_ago: float | None = None
    registered_at: float | None = None

    # Present when an operator has taken this node out of rotation. The
    # node is still listed — a switch whose subject disappears cannot be
    # switched back — so the browser needs to know which state it is in.
    drain: NodeDrainView | None = None

    @classmethod
    def of(cls, status: BackendStatus) -> 'NodeStatusView':
        return cls(**status.model_dump(), utilization=status.utilization)


class InferenceStatusView(BaseModel):
    """Every node, its totals, and whether registration is available at all.

    The totals are what answers "do we need another GPU?", which is the
    question this page exists for. Per-node rows answer the follow-up:
    which one.

    ``registration_enabled`` lets the portal explain an empty registered
    list correctly: "nothing has registered" and "this deployment does
    not accept registrations" look identical otherwise, and they call for
    completely different fixes.
    """

    nodes: list[NodeStatusView]
    total_in_flight: int
    total_capacity: int
    utilization: float
    registration_enabled: bool
    heartbeat_interval_seconds: int

    @classmethod
    def of(
        cls, snapshot: FleetSnapshot, *, registration_enabled: bool, heartbeat_interval_seconds: int
    ) -> 'InferenceStatusView':
        """Project a fleet snapshot for the portal."""
        return cls(
            nodes=[NodeStatusView.of(s) for s in snapshot.nodes],
            total_in_flight=snapshot.total_in_flight,
            total_capacity=snapshot.total_capacity,
            utilization=snapshot.utilization,
            registration_enabled=registration_enabled,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )


class NodeProbeRequest(BaseModel):
    """Ask a node what it is before adding it.

    The point of the flow: an admin pastes an address, and the node
    itself supplies the model ids. Typing them by hand is how you end up
    with a node that is never selected — a mistyped id simply never
    matches a request — and it is the only practical way to get LoRA
    adapter names right, since they are whatever the operator passed to
    ``--lora-modules``.
    """

    base_url: str
    api_key: str | None = None

    @model_validator(mode='after')
    def _base_url_is_absolute(self) -> 'NodeProbeRequest':
        if not self.base_url.startswith(('http://', 'https://')):
            raise ValueError('base_url must start with http:// or https://')
        return self


class NodeProbeResult(BaseModel):
    """What the node said, or why it did not.

    ``reachable`` false is a 200, not an error: "I could not reach that
    address" is a successful answer to "what is at this address", and the
    dialog needs the reason to show rather than an exception.
    """

    reachable: bool
    models: list[str] = []
    detail: str | None = None
    latency_ms: float | None = None

    # It answered, but refused without a token. The most likely outcome
    # of pasting the address of a vLLM node started with ``--api-key``,
    # and worth saying plainly: without this the dialog reported
    # "reachable, 0 models", which sounds like a node serving nothing.
    needs_credentials: bool = False


class AvailableModels(BaseModel):
    """Model ids this deployment serves, deduplicated and sorted.

    Includes LoRA adapters, since a node answers to each of them by name
    and any of them is a legitimate choice for a group.
    """

    models: list[str] = []
