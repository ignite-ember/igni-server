"""How a backend authenticates: the methods each kind offers, and what each one needs.

Every method is named, so the portal can offer exactly the ones a kind
takes and an operator reading a configuration sees which is in use —
"no credential stored" no longer has to mean "the server's own identity"
by convention.

A method needs some *settings* — not secret, shown back in the portal: a
client id, a service account's email — and at most one *secret*, kept
encrypted and never returned: the backend's ``api_key`` (sent as a key)
or its ``credential`` (handed to the cloud's SDK).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.services.ai.providers.kinds import BackendKind


class AuthMethod(StrEnum):
    """How a request to a backend is authenticated."""

    #: Nothing: a vLLM node on a private network.
    NONE = 'none'
    #: A static key: a Bearer token, or an Azure resource's `api-key`.
    API_KEY = 'api_key'
    #: The identity the server runs as, through the cloud's default chain:
    #: Workload Identity or attached service account on GCP, the system
    #: Managed Identity on Azure.
    AMBIENT = 'ambient'
    #: Azure: a user-assigned Managed Identity, by its client id.
    MANAGED_IDENTITY = 'managed_identity'
    #: Azure: an app registration's client secret.
    CLIENT_SECRET = 'client_secret'
    #: GCP: the server's identity acting as another service account.
    IMPERSONATE = 'impersonate'
    #: GCP: a service account's JSON key.
    SERVICE_ACCOUNT_KEY = 'service_account_key'
    #: AWS: the server's identity assumes an IAM role, by its ARN (and an
    #: external ID when the role's trust policy asks for one). Nothing secret
    #: is stored, and the role may be in another account.
    ASSUME_ROLE = 'assume_role'
    #: AWS: an IAM user's access key ID and secret access key.
    ACCESS_KEYS = 'access_keys'

    @property
    def label(self) -> str:
        """The method as a sentence names it, for the person reading a health detail."""
        return _LABELS[self]

    @property
    def is_token(self) -> bool:
        """Signed with credentials the server obtains and renews, rather than static ones or nothing."""
        return self not in (AuthMethod.NONE, AuthMethod.API_KEY, AuthMethod.ACCESS_KEYS)


#: How a sentence names each method.
_LABELS: Mapping[AuthMethod, str] = MappingProxyType(
    {
        AuthMethod.NONE: 'no credentials',
        AuthMethod.API_KEY: 'the stored key',
        AuthMethod.AMBIENT: "the server's own cloud identity",
        AuthMethod.MANAGED_IDENTITY: 'the user-assigned managed identity',
        AuthMethod.CLIENT_SECRET: "the app registration's client secret",
        AuthMethod.IMPERSONATE: 'the impersonated service account',
        AuthMethod.SERVICE_ACCOUNT_KEY: 'the stored service-account key',
        AuthMethod.ASSUME_ROLE: 'the assumed IAM role',
        AuthMethod.ACCESS_KEYS: 'the stored access keys',
    }
)


#: Where a method's secret lives on the backend, if it has one.
SecretSlot = Literal['api_key', 'credential'] | None


class MethodSpec(BaseModel):
    """What one method needs: its settings, by name, and where its secret goes."""

    model_config = ConfigDict(frozen=True)

    settings: tuple[str, ...] = ()
    secret: SecretSlot = None
    #: Settings it takes but does not need: a role's external ID.
    optional: tuple[str, ...] = ()


def _offered(*methods: tuple[AuthMethod, MethodSpec]) -> Mapping[AuthMethod, MethodSpec]:
    """A kind's methods, read-only, in the order given."""
    return MappingProxyType(dict(methods))


_AZURE = _offered(
    (AuthMethod.AMBIENT, MethodSpec()),
    (AuthMethod.MANAGED_IDENTITY, MethodSpec(settings=('client_id',))),
    (AuthMethod.CLIENT_SECRET, MethodSpec(settings=('tenant_id', 'client_id'), secret='credential')),
    (AuthMethod.API_KEY, MethodSpec(secret='api_key')),
)
_VERTEX = _offered(
    (AuthMethod.AMBIENT, MethodSpec()),
    (AuthMethod.IMPERSONATE, MethodSpec(settings=('service_account',))),
    (AuthMethod.SERVICE_ACCOUNT_KEY, MethodSpec(secret='credential')),
)

#: Each kind's methods, in the order the portal offers them; the first is the kind's default.
METHODS: Mapping[BackendKind, Mapping[AuthMethod, MethodSpec]] = MappingProxyType(
    {
        BackendKind.OPENAI: _offered(
            (AuthMethod.NONE, MethodSpec()),
            (AuthMethod.API_KEY, MethodSpec(secret='api_key')),
        ),
        BackendKind.AZURE_OPENAI: _AZURE,
        BackendKind.VERTEX_OPENAI: _VERTEX,
        BackendKind.ANTHROPIC: _offered((AuthMethod.API_KEY, MethodSpec(secret='api_key'))),
        BackendKind.BEDROCK: _offered(
            (AuthMethod.AMBIENT, MethodSpec()),
            (AuthMethod.ASSUME_ROLE, MethodSpec(settings=('role_arn',), optional=('external_id',))),
            (AuthMethod.ACCESS_KEYS, MethodSpec(settings=('access_key_id',), secret='credential')),
        ),
        # Claude on Vertex and in Foundry signs in exactly as the OpenAI-wire models there do.
        BackendKind.VERTEX_ANTHROPIC: _VERTEX,
        BackendKind.AZURE_ANTHROPIC: _AZURE,
    }
)


def default_method(kind: BackendKind, *, has_api_key: bool, has_credential: bool) -> AuthMethod:
    """The method an operator's backend definition means when it names none.

    A static backend list or a registering GPU node may leave the method
    out and say it by what it carries: a key means the key, a stored
    credential means the one method that stores one with no settings, and
    nothing means no auth on a GPU node and the server's own identity on a
    cloud. Stored rows always name theirs (migration 0030 backfilled it).
    """
    methods = METHODS[kind]
    if has_api_key and AuthMethod.API_KEY in methods:
        return AuthMethod.API_KEY
    if has_credential:
        stored = [method for method, spec in methods.items() if spec.secret == 'credential' and not spec.settings]
        if stored:
            return stored[0]
    return next(iter(methods))
