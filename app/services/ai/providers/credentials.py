"""A cloud backend's access token: obtained the way its auth method says, cached, renewed.

The methods (`providers/auth.py`) that sign with a token:

* **ambient** — the identity the server already runs as: Workload Identity
  or an attached service account on GCP, the system Managed Identity on
  Azure, through each SDK's default chain. Nothing secret is kept by igni.
* **managed_identity** — Azure: a user-assigned Managed Identity, by client id.
* **client_secret** — Azure: an app registration's tenant, client id and secret.
* **impersonate** — GCP: the server's identity acting as another service account.
* **service_account_key** — GCP: a service account's JSON key.

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

from app.services.ai.providers.auth import AuthMethod
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

#: Builds a backend's `TokenFetch` from its method's settings and its secret (or None).
FetchFactory = Callable[[Mapping[str, str], str | None], TokenFetch]


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


def google_fetch(method: AuthMethod) -> FetchFactory:
    """Vertex AI's token for *method*: Google's default chain, an impersonated account, or a key."""

    def build(settings: Mapping[str, str], secret: str | None) -> TokenFetch:
        credentials: Any = None

        def refreshed() -> AccessToken:
            nonlocal credentials
            import google.auth
            import google.auth.transport.requests

            if credentials is None:
                if method is AuthMethod.SERVICE_ACCOUNT_KEY:
                    from google.oauth2 import service_account

                    info = json.loads(secret or '')
                    credentials = service_account.Credentials.from_service_account_info(info, scopes=[GOOGLE_SCOPE])
                else:
                    credentials, _project = google.auth.default(scopes=[GOOGLE_SCOPE])
                    if method is AuthMethod.IMPERSONATE:
                        from google.auth import impersonated_credentials

                        credentials = impersonated_credentials.Credentials(
                            source_credentials=credentials,
                            target_principal=settings['service_account'],
                            target_scopes=[GOOGLE_SCOPE],
                        )
            credentials.refresh(google.auth.transport.requests.Request())
            expiry = credentials.expiry
            # google-auth's expiry is a naive UTC datetime.
            expires_at = calendar.timegm(expiry.utctimetuple()) if expiry is not None else time.time() + 300
            return AccessToken(value=credentials.token, expires_at=float(expires_at))

        async def fetch() -> AccessToken:
            # google-auth refreshes synchronously, over `requests`.
            return await asyncio.to_thread(refreshed)

        return fetch

    return build


def azure_fetch(method: AuthMethod) -> FetchFactory:
    """Azure's token for *method*: the default chain, a user-assigned identity, or an app's secret."""

    def build(settings: Mapping[str, str], secret: str | None) -> TokenFetch:
        credential: Any = None

        async def fetch() -> AccessToken:
            nonlocal credential
            from azure.identity.aio import ClientSecretCredential, DefaultAzureCredential, ManagedIdentityCredential

            if credential is None:
                if method is AuthMethod.CLIENT_SECRET:
                    credential = ClientSecretCredential(
                        tenant_id=settings['tenant_id'], client_id=settings['client_id'], client_secret=secret or ''
                    )
                elif method is AuthMethod.MANAGED_IDENTITY:
                    credential = ManagedIdentityCredential(client_id=settings['client_id'])
                else:
                    credential = DefaultAzureCredential()
            token = await credential.get_token(AZURE_SCOPE)
            return AccessToken(value=token.token, expires_at=float(token.expires_on))

        return fetch

    return build


_GOOGLE = (AuthMethod.AMBIENT, AuthMethod.IMPERSONATE, AuthMethod.SERVICE_ACCOUNT_KEY)
_AZURE = (AuthMethod.AMBIENT, AuthMethod.MANAGED_IDENTITY, AuthMethod.CLIENT_SECRET)

#: The token fetch for each kind and method that signs with a token.
_FETCHES: dict[tuple[BackendKind, AuthMethod], FetchFactory] = {
    **{(BackendKind.VERTEX_OPENAI, method): google_fetch(method) for method in _GOOGLE},
    **{(BackendKind.AZURE_OPENAI, method): azure_fetch(method) for method in _AZURE},
}


@dataclass(frozen=True)
class Signer:
    """Who a token is for: the backend, its kind and method, the method's settings and secret."""

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
    """Every cloud backend's cached token, one per backend, method, settings and secret.

    An operator changing any of those gets a fresh token at once rather
    than when the old one expires.
    """

    def __init__(
        self,
        *,
        fetches: Mapping[tuple[BackendKind, AuthMethod], FetchFactory] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._fetches = dict(_FETCHES if fetches is None else fetches)
        self._clock = clock
        self._tokens: dict[tuple[str, BackendKind, AuthMethod, str], CachedToken] = {}

    async def bearer(self, signer: Signer) -> str:
        """The backend's current token. Raises `CredentialUnavailable` when the cloud gives none."""
        try:
            cached = self._cached(signer)
            return await cached.value()
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
        """Drop the backend's token after the provider refused it."""
        cached = self._tokens.get(signer.key())
        if cached is not None:
            cached.invalidate()

    def _cached(self, signer: Signer) -> CachedToken:
        key = signer.key()
        if key not in self._tokens:
            fetch = self._fetches[(signer.kind, signer.auth)](signer.settings, signer.secret)
            self._tokens[key] = CachedToken(fetch, clock=self._clock)
        return self._tokens[key]


_SHARED = CloudCredentials()


def shared_credentials() -> CloudCredentials:
    """The process's one cache, so the chat proxy and the health probe share tokens."""
    return _SHARED
