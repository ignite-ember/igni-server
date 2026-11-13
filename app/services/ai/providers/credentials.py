"""A cloud backend's access token: obtained from the cloud's own identity, cached, renewed.

Two sources per cloud, decided by whether the backend carries a stored
credential:

* **ambient** (none stored) — the identity the server already runs as:
  Workload Identity or a service account's metadata on GCP, a Managed
  Identity on Azure, or whatever each SDK's default chain finds. Nothing
  secret is kept by igni.
* **stored** — for a server running outside that cloud: a GCP
  service-account key, or an Azure app's client secret, kept encrypted on
  the backend like an `api_key`.

The SDKs are imported inside the fetch, so a deployment with no cloud
backend never loads them. A token is renewed `REFRESH_MARGIN_SECONDS`
before it runs out, and `invalidate` drops it after the provider refuses
it, so one 401 costs one renewal and not a failed request.
"""

from __future__ import annotations

import asyncio
import calendar
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import logging
import time
from typing import Any

from app.services.ai.providers.kinds import BackendKind

logger = logging.getLogger(__name__)

#: Renew a token this many seconds before it expires.
REFRESH_MARGIN_SECONDS = 300

#: The OAuth scope Vertex AI accepts.
GOOGLE_SCOPE = 'https://www.googleapis.com/auth/cloud-platform'

#: The Entra ID scope Azure OpenAI (and Foundry) accept.
AZURE_SCOPE = 'https://cognitiveservices.azure.com/.default'


@dataclass(frozen=True)
class AccessToken:
    """A bearer token and the moment it stops working, in epoch seconds."""

    value: str
    expires_at: float


#: Gets a fresh token. Built once per backend, called on each renewal.
TokenFetch = Callable[[], Awaitable[AccessToken]]

#: Builds a backend's `TokenFetch` from its stored credential, or None for ambient.
FetchFactory = Callable[[dict[str, Any] | None], TokenFetch]


class CredentialUnavailable(Exception):
    """The server could not get a token for a cloud backend.

    The message names the kind and never the credential: it reaches logs
    and, through `UpstreamCredentialsUnavailable`, a caller's error.
    """

    def __init__(self, kind: BackendKind) -> None:
        self.kind = kind
        super().__init__(f'No credentials for the {kind.value} backend')


class CachedToken:
    """One backend's token, fetched when missing or about to run out."""

    def __init__(self, fetch: TokenFetch, *, clock: Callable[[], float] = time.time) -> None:
        self._fetch = fetch
        self._clock = clock
        self._token: AccessToken | None = None
        self._lock = asyncio.Lock()

    async def value(self) -> str:
        """The current token, renewed first if it is within the margin of expiring."""
        async with self._lock:
            if self._token is None or self._token.expires_at - REFRESH_MARGIN_SECONDS <= self._clock():
                self._token = await self._fetch()
            return self._token.value

    def invalidate(self) -> None:
        """Forget the token, so the next `value` fetches a new one."""
        self._token = None


def google_fetch(stored: dict[str, Any] | None) -> TokenFetch:
    """Vertex AI's token: the service-account key if one is stored, else Google's default chain."""
    credentials: Any = None

    def refreshed() -> AccessToken:
        nonlocal credentials
        import google.auth
        import google.auth.transport.requests
        from google.oauth2 import service_account

        if credentials is None:
            if stored is not None:
                credentials = service_account.Credentials.from_service_account_info(stored, scopes=[GOOGLE_SCOPE])
            else:
                credentials, _project = google.auth.default(scopes=[GOOGLE_SCOPE])
        credentials.refresh(google.auth.transport.requests.Request())
        expiry = credentials.expiry
        # google-auth's expiry is a naive UTC datetime.
        expires_at = calendar.timegm(expiry.utctimetuple()) if expiry is not None else time.time() + 300
        return AccessToken(value=credentials.token, expires_at=float(expires_at))

    async def fetch() -> AccessToken:
        # google-auth refreshes synchronously, over `requests`.
        return await asyncio.to_thread(refreshed)

    return fetch


def azure_fetch(stored: dict[str, Any] | None) -> TokenFetch:
    """Azure's token: the app's client secret if one is stored, else the default chain (Managed Identity first)."""
    credential: Any = None

    async def fetch() -> AccessToken:
        nonlocal credential
        from azure.identity.aio import ClientSecretCredential, DefaultAzureCredential

        if credential is None:
            if stored is not None:
                credential = ClientSecretCredential(
                    tenant_id=stored['tenant_id'],
                    client_id=stored['client_id'],
                    client_secret=stored['client_secret'],
                )
            else:
                credential = DefaultAzureCredential()
        token = await credential.get_token(AZURE_SCOPE)
        return AccessToken(value=token.token, expires_at=float(token.expires_on))

    return fetch


_FETCHES: dict[BackendKind, FetchFactory] = {
    BackendKind.VERTEX_OPENAI: google_fetch,
    BackendKind.AZURE_OPENAI: azure_fetch,
}


class CloudCredentials:
    """Every cloud backend's cached token, one per backend and credential.

    Keyed by the backend, its kind and a digest of its stored credential,
    so an operator replacing the credential gets a fresh token at once
    rather than when the old one expires.
    """

    def __init__(
        self,
        *,
        fetches: Mapping[BackendKind, FetchFactory] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._fetches = dict(_FETCHES if fetches is None else fetches)
        self._clock = clock
        self._tokens: dict[tuple[str, BackendKind, str], CachedToken] = {}

    async def bearer(self, *, backend_id: str, kind: BackendKind, credential: str | None) -> str:
        """The backend's current token. Raises `CredentialUnavailable` when the cloud gives none."""
        cached = self._cached(backend_id=backend_id, kind=kind, credential=credential)
        try:
            return await cached.value()
        except Exception as exc:  # noqa: BLE001 — every SDK has its own error family; all mean the same here
            # The exception's type and text, never the credential: none of
            # these SDKs put the secret in their messages.
            logger.warning(
                'Could not get a token for backend %s (%s): %s: %s', backend_id, kind.value, type(exc).__name__, exc
            )
            raise CredentialUnavailable(kind) from exc

    def invalidate(self, *, backend_id: str, kind: BackendKind, credential: str | None) -> None:
        """Drop the backend's token after the provider refused it."""
        key = _key(backend_id, kind, credential)
        if key in self._tokens:
            self._tokens[key].invalidate()

    def _cached(self, *, backend_id: str, kind: BackendKind, credential: str | None) -> CachedToken:
        key = _key(backend_id, kind, credential)
        if key not in self._tokens:
            stored = json.loads(credential) if credential else None
            self._tokens[key] = CachedToken(self._fetches[kind](stored), clock=self._clock)
        return self._tokens[key]


def _key(backend_id: str, kind: BackendKind, credential: str | None) -> tuple[str, BackendKind, str]:
    return backend_id, kind, hashlib.sha256((credential or '').encode()).hexdigest()


_SHARED = CloudCredentials()


def shared_credentials() -> CloudCredentials:
    """The process's one cache, so the chat proxy and the health probe share tokens."""
    return _SHARED
