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

from app.services.ai.providers.auth import AuthMethod
from app.services.ai.providers.azure import (
    AzureClientSecretSource,
    AzureDefaultSource,
    AzureManagedIdentitySource,
)
from app.services.ai.providers.google import GoogleDefaultSource, GoogleImpersonatedSource, GoogleKeySource
from app.services.ai.providers.kinds import BackendKind
from app.services.ai.providers.tokens import CachedToken, CredentialUnavailable, TokenSource

logger = logging.getLogger(__name__)

#: The source for each kind and method that signs with a credential the server obtains.
TOKEN_SOURCES: Mapping[tuple[BackendKind, AuthMethod], type[TokenSource[str]]] = MappingProxyType(
    {
        (BackendKind.VERTEX_OPENAI, AuthMethod.AMBIENT): GoogleDefaultSource,
        (BackendKind.VERTEX_OPENAI, AuthMethod.IMPERSONATE): GoogleImpersonatedSource,
        (BackendKind.VERTEX_OPENAI, AuthMethod.SERVICE_ACCOUNT_KEY): GoogleKeySource,
        (BackendKind.AZURE_OPENAI, AuthMethod.AMBIENT): AzureDefaultSource,
        (BackendKind.AZURE_OPENAI, AuthMethod.MANAGED_IDENTITY): AzureManagedIdentitySource,
        (BackendKind.AZURE_OPENAI, AuthMethod.CLIENT_SECRET): AzureClientSecretSource,
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

    def key(self) -> tuple[str, BackendKind, AuthMethod, str]:
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
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._sources = sources
        self._clock = clock
        self._tokens: dict[tuple[str, BackendKind, AuthMethod, str], CachedToken[str]] = {}

    async def bearer(self, signer: Signer) -> str:
        """The backend's current token. Raises `CredentialUnavailable` when the cloud gives none."""
        try:
            return await self._cached(signer).value()
        except Exception as exc:  # noqa: BLE001 — every SDK has its own error family; all mean the same here
            # The exception's type and text, never the secret: none of these
            # SDKs put it in their messages.
            logger.warning(
                'Could not get a token for backend %s (%s, %s): %s: %s',
                signer.backend_id,
                signer.kind.value,
                signer.auth.value,
                type(exc).__name__,
                exc,
            )
            raise CredentialUnavailable(signer.kind) from exc

    def invalidate(self, signer: Signer) -> None:
        """Drop the backend's credential after the provider refused it."""
        cached = self._tokens.get(signer.key())
        if cached is not None:
            cached.invalidate()

    def _cached(self, signer: Signer) -> CachedToken[str]:
        key = signer.key()
        if key not in self._tokens:
            source = self._sources[(signer.kind, signer.auth)](settings=signer.settings, secret=signer.secret)
            self._tokens[key] = CachedToken(source, clock=self._clock)
        return self._tokens[key]
