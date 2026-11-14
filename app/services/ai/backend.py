"""A backend the router sends to, and the lease one request holds on it.

`InferenceBackend` is a backend as the operator defined it — in the
static list, by a registering node, or in the portal — and its validators
are the one place the rules for each kind and auth method live, so the
three sources cannot disagree. `BackendLease` is what one request carries
of it: enough to address and sign the call, and to give the slot back.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, model_validator, ValidationError

from app.services.ai.providers.auth import AuthMethod, default_method, METHODS
from app.services.ai.providers.credentials import Signer
from app.services.ai.providers.kinds import BackendKind, Wire

#: Anthropic's own API, for an ``anthropic`` backend that names no other address.
ANTHROPIC_API = 'https://api.anthropic.com'


class BackendInvalid(ValueError):
    """A backend definition the router could not use. Says what is wrong, never a value."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__('; '.join(reasons))


class InferenceBackend(BaseModel):
    """One inference endpoint: the customer's own node, or a cloud's model service.

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

    # A validation error names what is wrong, never the value: the value
    # may be a key, a client secret or a service-account key.
    model_config = ConfigDict(hide_input_in_errors=True)

    id: str

    # Who serves this backend. ``openai`` — the default, and every backend
    # there was before kinds — is an OpenAI-compatible URL with an optional
    # Bearer key. The cloud kinds are signed with the cloud's own
    # credentials; see ``app/services/ai/providers``.
    kind: BackendKind = BackendKind.OPENAI

    # Where requests go. Built for a kind whose address its settings fix —
    # Vertex from its project and region, Bedrock from its region, Anthropic's
    # own API when none is named (``_addressed_by_its_kind``); required for
    # every other kind.
    base_url: str = ''

    # Where the model runs — Vertex AI and Bedrock — and, for Vertex, in which project.
    region: str | None = None
    project: str | None = None

    # How requests to this backend are authenticated; each kind offers its
    # own set (``providers/auth.py``). A definition that names none means
    # what ``default_method`` says it means.
    auth: AuthMethod | None = None

    # The method's settings — not secret, shown back in the portal: an
    # Azure client id and tenant, the service account to impersonate.
    auth_settings: dict[str, str] = Field(default_factory=dict)

    # The method's secret when it is handed to the cloud's SDK rather than
    # sent as a key: an Azure app's client secret, a GCP service-account
    # key (JSON). Kept encrypted, never returned.
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

    @classmethod
    def validated(cls, **fields: object) -> InferenceBackend:
        """A backend from *fields*, or `BackendInvalid` naming every rule it breaks."""
        try:
            return cls.model_validate(fields)
        except ValidationError as exc:
            raise BackendInvalid([error['msg'].removeprefix('Value error, ') for error in exc.errors()]) from None

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

    @property
    def signer(self) -> Signer:
        """Who a credential for this backend is for."""
        return Signer(
            backend_id=self.id,
            kind=self.kind,
            auth=self.auth or AuthMethod.NONE,
            settings=self.auth_settings,
            secret=self.credential,
        )

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

    @model_validator(mode='before')
    @classmethod
    def _addressed_by_its_kind(cls, data):
        """Give a backend whose kind fixes its address the address its settings make, when it names none.

        Vertex AI is the same path in every project, so an operator names the
        project and the region rather than pasting a URL; ``global`` has no
        regional prefix. Bedrock is one host per region. Anthropic's own API
        has one address, so naming none means it. A base URL given anyway
        wins: a private endpoint is still that provider. Whether the project
        and region are there at all is `_fits_its_kind`'s to say.
        """
        if not isinstance(data, dict) or data.get('base_url'):
            return data
        kind, project, region = data.get('kind'), data.get('project'), data.get('region')
        if kind in (BackendKind.VERTEX_OPENAI, BackendKind.VERTEX_ANTHROPIC) and project and region:
            host = (
                'https://aiplatform.googleapis.com'
                if region == 'global'
                else f'https://{region}-aiplatform.googleapis.com'
            )
            path = 'endpoints/openapi' if kind == BackendKind.VERTEX_OPENAI else 'publishers/anthropic/models'
            return {**data, 'base_url': f'{host}/v1/projects/{project}/locations/{region}/{path}'}
        if kind == BackendKind.BEDROCK and region:
            return {**data, 'base_url': f'https://bedrock-runtime.{region}.amazonaws.com'}
        if kind == BackendKind.ANTHROPIC:
            return {**data, 'base_url': ANTHROPIC_API}
        return data

    @model_validator(mode='after')
    def _fits_its_kind(self) -> InferenceBackend:
        """Refuse a field the kind or the method would silently ignore, and a field it needs left out.

        A Vertex backend given an ``api_key`` would send Google's token and
        never the key, and an operator reading the configuration would
        believe the key was in use. Saying so at load time is cheaper than
        that afternoon.
        """
        where = f'Backend {self.id!r} ({self.kind.value})'
        if self.kind.is_vertex and not (self.project and self.region):
            raise ValueError(f'{where}: a Vertex backend needs its project and region')
        if self.kind is BackendKind.BEDROCK and not self.region:
            raise ValueError(f'{where}: a Bedrock backend needs its region')
        if self.project and not self.kind.is_vertex:
            raise ValueError(f'{where}: project is for a Vertex backend only')
        if self.region and not self.kind.has_region:
            raise ValueError(f'{where}: region is for a Vertex or Bedrock backend only')
        if not self.base_url.startswith(('http://', 'https://')):
            raise ValueError(f'{where}: base_url must start with http:// or https://')
        if self.auth is None:
            self.auth = default_method(self.kind, has_api_key=bool(self.api_key), has_credential=bool(self.credential))
        self._auth_fits(where, self.auth)
        unknown = sorted(set(self.model_ids) - set(self.models))
        if unknown:
            raise ValueError(f'{where}: model_ids names models it does not serve: {", ".join(unknown)}')
        return self

    def _auth_fits(self, where: str, auth: AuthMethod) -> None:
        """The method is one the kind offers, with exactly its settings and its secret. Never echoes a secret."""
        methods = METHODS[self.kind]
        spec = methods.get(auth)
        if spec is None:
            offered = ', '.join(method.value for method in methods)
            raise ValueError(f'{where}: auth {auth.value!r} is not offered for this kind; it takes {offered}')
        method = f'{where} with {auth.value} auth'
        missing = [name for name in spec.settings if not self.auth_settings.get(name)]
        if missing:
            raise ValueError(f'{method}: needs {", ".join(missing)}')
        extra = sorted(set(self.auth_settings) - set(spec.settings) - set(spec.optional))
        if extra:
            raise ValueError(f'{method}: does not take {", ".join(extra)}')
        if spec.secret == 'api_key' and not self.api_key:
            raise ValueError(f'{method}: needs its key')
        if spec.secret != 'api_key' and self.api_key:
            raise ValueError(f'{method}: takes no api_key')
        if spec.secret == 'credential' and not self.credential:
            raise ValueError(f'{method}: needs its credential')
        if spec.secret != 'credential' and self.credential:
            raise ValueError(f'{method}: takes no stored credential')
        if auth is AuthMethod.SERVICE_ACCOUNT_KEY:
            self._is_a_service_account_key(method)

    def _is_a_service_account_key(self, method: str) -> None:
        try:
            stored = json.loads(self.credential or '')
        except ValueError:
            raise ValueError(f'{method}: the stored credential is not JSON') from None
        if not isinstance(stored, dict) or stored.get('type') != 'service_account':
            raise ValueError(f'{method}: the stored credential is not a Google service-account key')


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
    #: Bedrock signs for its region.
    region: str | None = None
    auth: AuthMethod = AuthMethod.NONE
    auth_settings: dict[str, str] = Field(default_factory=dict)
    credential: str | None = Field(default=None, repr=False)
    model_ids: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def for_backend(cls, backend: InferenceBackend, *, lease_id: str, model: str) -> BackendLease:
        """The lease one request holds on *backend* for *model*: the one place a backend becomes a lease."""
        return cls(
            lease_id=lease_id,
            backend_id=backend.id,
            base_url=backend.base_url,
            model=model,
            api_key=backend.api_key,
            kind=backend.kind,
            region=backend.region,
            auth=backend.auth or AuthMethod.NONE,
            auth_settings=dict(backend.auth_settings),
            credential=backend.credential,
            model_ids=dict(backend.model_ids),
        )

    def upstream_model(self, model: str) -> str:
        """The name the provider knows *model* by."""
        return self.model_ids.get(model, model)

    @property
    def completions_url(self) -> str:
        """The node's OpenAI-compatible chat completions endpoint."""
        return f'{self.base_url.rstrip("/")}/chat/completions'

    @property
    def signer(self) -> Signer:
        """Who a credential for this lease's backend is for."""
        return Signer(
            backend_id=self.backend_id,
            kind=self.kind,
            auth=self.auth,
            settings=self.auth_settings,
            secret=self.credential,
        )

    @property
    def renews_token(self) -> bool:
        """Whether a 401 or 403 may be a credential the server obtained and should renew.

        A static key refused is refused for good, and asking again would
        only repeat it.
        """
        return self.auth.is_token

    def auth_headers(self) -> dict[str, str]:
        """Headers for a call to this backend that the server needs no credential for.

        No ``Authorization`` when the node has no key: a vLLM on a private
        network has no auth, and ``Bearer None`` would be a confusing 401.
        An Azure resource's key goes in Azure's own ``api-key`` header.
        """
        headers = {'Content-Type': 'application/json'}
        if self.kind is BackendKind.AZURE_OPENAI and self.auth is AuthMethod.API_KEY:
            headers['api-key'] = self.api_key or ''
        elif self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        return headers
