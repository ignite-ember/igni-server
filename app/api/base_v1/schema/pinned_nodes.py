"""The portal's backends: what an admin adds and edits, what it gets back, and what each kind offers.

Apart from ``nodes.py``'s registration and fleet-status shapes because
these carry secrets in, and must never carry them out: ``api_key`` and
``credential`` go in and come back only as ``has_api_key`` and
``has_credential``. A refusal never echoes them either — inputs are
hidden in validation errors, and the backend's own rules answer in their
own words (``InferenceBackend.validated``).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.sql.models.inference_node import InferenceNode
from app.db.sql.schemas.inference_node import NodeChanges
from app.services.ai.backend import InferenceBackend
from app.services.ai.providers.auth import AuthMethod, METHODS, SecretSlot
from app.services.ai.providers.kinds import BackendKind, Wire


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
    base_url: str = Field(
        default='',
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
        """The node as the router will see it. Raises `BackendInvalid` naming every rule it breaks."""
        return InferenceBackend.validated(
            id=self.node_id,
            kind=self.kind,
            base_url=self.base_url,
            models=self.models,
            api_key=self.api_key or None,
            max_concurrent=self.max_concurrent,
            multimodal=self.multimodal,
            region=self.region,
            project=self.project,
            credential=self.credential or None,
            model_ids=self.model_ids,
            auth=self.auth,
            auth_settings=self.auth_settings,
        )


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

        `exclude_unset` is the whole semantics: absent means unchanged, and
        an explicitly empty `api_key` means clear it. Dumping everything
        would turn every edit into a write of every field.
        """
        return NodeChanges(**self.model_dump(exclude_unset=True))


class PinnedNodeView(BaseModel):
    """A node added here, as the portal gets it back.

    No ``api_key`` or ``credential``: they go in and are never returned.
    An admin who needs to change one sets a new one.
    """

    node_id: str
    base_url: str
    models: list[str]
    max_concurrent: int
    multimodal: bool
    enabled: bool
    has_api_key: bool
    kind: BackendKind
    region: str | None
    project: str | None
    model_ids: dict[str, str]
    auth: AuthMethod
    auth_settings: dict[str, str]
    # As ``has_api_key``: whether one is stored, never what it is.
    has_credential: bool

    @classmethod
    def of(cls, node: InferenceNode) -> PinnedNodeView:
        """Project a stored node for the portal."""
        return cls(
            node_id=str(node.node_id),
            base_url=str(node.base_url),
            models=list(node.models or []),
            max_concurrent=int(node.max_concurrent),
            multimodal=bool(node.multimodal),
            enabled=bool(node.enabled),
            has_api_key=bool(node.api_key),
            kind=BackendKind(node.kind),
            region=node.region,
            project=node.project,
            model_ids=dict(node.model_ids),
            auth=AuthMethod(node.auth),
            auth_settings=dict(node.auth_settings),
            has_credential=bool(node.credential),
        )


class AuthMethodView(BaseModel):
    """One way a kind of backend can authenticate, for the portal to build its form from."""

    method: AuthMethod
    #: Settings the method needs, not secret.
    settings: list[str]
    #: Where its secret goes, or none.
    secret: SecretSlot


class BackendKindView(BaseModel):
    """A kind of backend and the methods it offers, the first being its default."""

    kind: BackendKind
    wire: Wire
    methods: list[AuthMethodView]

    @classmethod
    def every(cls) -> list[BackendKindView]:
        """Every kind, from the server's own table."""
        return [
            cls(
                kind=kind,
                wire=kind.wire,
                methods=[
                    AuthMethodView(method=method, settings=list(spec.settings), secret=spec.secret)
                    for method, spec in methods.items()
                ],
            )
            for kind, methods in METHODS.items()
        ]
