"""Every cloud backend's credential, cached and renewed, behind one object built at startup.

The methods (`providers/auth.py`) that sign with a credential the server
obtains, and the source each one reads (`google.py`, `azure.py`):

* **ambient** — the identity the server already runs as: Workload Identity
  or an attached service account on GCP, the system Managed Identity on
  Azure. Nothing secret is kept by igni.
* **managed_identity** — Azure: a user-assigned Managed Identity, by client id.
* **client_secret** — Azure: an app registration's tenant, client id and secret.
* **impersonate** — GCP: the server's identity acting as another service account.
* **service_account_key** — GCP: a service account's JSON key.
* **assume_role** — AWS: the server's identity assumes an IAM role (STS),
  with an external ID when the role's trust policy asks for one.
* **access_keys** — AWS: an IAM user's access key, stored encrypted.

A Bedrock request is signed with keys rather than a token (`aws.py`), so
those come from `aws` and the rest from `bearer`, each from its own cache.

One `CloudCredentials` is built by the chat services, the app's
composition root, and handed to everything that signs or probes, so the
proxy and the health probe share one cache without a module global.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import logging
import time
from types import MappingProxyType
from typing import TypeVar

from starlette.applications import Starlette

from app.services.ai.providers.auth import AuthMethod
from app.services.ai.providers.aws import AwsAccessKeysSource, AwsAssumedRoleSource, AwsDefaultSource, AwsKeys
from app.services.ai.providers.azure import (
    AzureClientSecretSource,
    AzureDefaultSource,
    AzureManagedIdentitySource,
)
from app.services.ai.providers.google import GoogleDefaultSource, GoogleImpersonatedSource, GoogleKeySource
from app.services.ai.providers.kinds import BackendKind
from app.services.ai.providers.tokens import CachedToken, CredentialUnavailable, TokenSource

T = TypeVar('T')

#: A credential's cache key: the backend, its kind and method, and a digest of its settings and secret.
SignerKey = tuple[str, BackendKind, AuthMethod, str]

logger = logging.getLogger(__name__)

_GOOGLE: Mapping[AuthMethod, type[TokenSource[str]]] = {
    AuthMethod.AMBIENT: GoogleDefaultSource,
    AuthMethod.IMPERSONATE: GoogleImpersonatedSource,
    AuthMethod.SERVICE_ACCOUNT_KEY: GoogleKeySource,
}
_AZURE: Mapping[AuthMethod, type[TokenSource[str]]] = {
    AuthMethod.AMBIENT: AzureDefaultSource,
    AuthMethod.MANAGED_IDENTITY: AzureManagedIdentitySource,
    AuthMethod.CLIENT_SECRET: AzureClientSecretSource,
}

#: The bearer-token source for each kind and method that signs with one.
TOKEN_SOURCES: Mapping[tuple[BackendKind, AuthMethod], type[TokenSource[str]]] = MappingProxyType(
    {
        **{
            (kind, method): source
            for kind in (BackendKind.VERTEX_OPENAI, BackendKind.VERTEX_ANTHROPIC)
            for method, source in _GOOGLE.items()
        },
        **{
            (kind, method): source
            for kind in (BackendKind.AZURE_OPENAI, BackendKind.AZURE_ANTHROPIC)
            for method, source in _AZURE.items()
        },
    }
)

#: The AWS key source for each Bedrock method.
KEY_SOURCES: Mapping[tuple[BackendKind, AuthMethod], type[TokenSource[AwsKeys]]] = MappingProxyType(
    {
        (BackendKind.BEDROCK, AuthMethod.AMBIENT): AwsDefaultSource,
        (BackendKind.BEDROCK, AuthMethod.ASSUME_ROLE): AwsAssumedRoleSource,
        (BackendKind.BEDROCK, AuthMethod.ACCESS_KEYS): AwsAccessKeysSource,
    }
)


@dataclass(frozen=True)
class Signer:
    """Who a credential is for: the backend, its kind and method, the method's settings and secret."""

    backend_id: str
    kind: BackendKind
    auth: AuthMethod
    settings: Mapping[str, str]
    secret: str | None

    def key(self) -> SignerKey:
        """A cache key that changes with any of them, without holding the secret itself."""
        material = json.dumps([sorted(self.settings.items()), self.secret or ''])
        return self.backend_id, self.kind, self.auth, hashlib.sha256(material.encode()).hexdigest()


class CloudCredentials:
    """Every cloud backend's cached credential, one per backend, method, settings and secret.

    An operator changing any of those gets a fresh credential at once
    rather than when the old one expires.
    """

    def __init__(
        self,
        *,
        sources: Mapping[tuple[BackendKind, AuthMethod], type[TokenSource[str]]] = TOKEN_SOURCES,
        key_sources: Mapping[tuple[BackendKind, AuthMethod], type[TokenSource[AwsKeys]]] = KEY_SOURCES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._sources = sources
        self._key_sources = key_sources
        self._clock = clock
        self._tokens: dict[SignerKey, CachedToken[str]] = {}
        self._keys: dict[SignerKey, CachedToken[AwsKeys]] = {}

    @classmethod
    def of_app(cls, app: Starlette) -> CloudCredentials:
        """The app's one cache, built on first use: the proxy signs with it and the health probe asks it.

        Its own slot on the app rather than a part of the chat services, so
        the fleet page asking for a token builds nothing else.
        """
        credentials: CloudCredentials | None = getattr(app.state, 'cloud_credentials', None)
        if credentials is None:
            credentials = app.state.cloud_credentials = cls()
        return credentials

    async def bearer(self, signer: Signer) -> str:
        """The backend's current token. Raises `CredentialUnavailable` when the cloud gives none."""
        return await self._value(signer, self._tokens, self._sources)

    async def aws(self, signer: Signer) -> AwsKeys:
        """A Bedrock backend's current keys. Raises `CredentialUnavailable` when AWS gives none."""
        return await self._value(signer, self._keys, self._key_sources)

    def invalidate(self, signer: Signer) -> None:
        """Drop the backend's credential after the provider refused it."""
        key = signer.key()
        for cache in (self._tokens, self._keys):
            cached = cache.get(key)
            if cached is not None:
                cached.invalidate()

    async def _value(
        self,
        signer: Signer,
        cache: dict[SignerKey, CachedToken[T]],
        sources: Mapping[tuple[BackendKind, AuthMethod], type[TokenSource[T]]],
    ) -> T:
        key = signer.key()
        try:
            if key not in cache:
                source = sources[(signer.kind, signer.auth)](settings=signer.settings, secret=signer.secret)
                cache[key] = CachedToken(source, clock=self._clock)
            return await cache[key].value()
        except Exception as exc:  # noqa: BLE001 — every SDK has its own error family; all mean the same here
            # The exception's type and text, never the secret: none of these
            # SDKs put it in their messages.
            logger.warning(
                'Could not get a credential for backend %s (%s, %s): %s: %s',
                signer.backend_id,
                signer.kind.value,
                signer.auth.value,
                type(exc).__name__,
                exc,
            )
            raise CredentialUnavailable(signer.kind) from exc
