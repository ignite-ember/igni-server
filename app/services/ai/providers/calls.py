"""One request to a backend, addressed and signed the way its auth method needs.

The chat client hands over the lease and the body; `ProviderCalls` says
where it goes and with which headers. Everything after that — the
timeouts, the stream, the status handling — is the same for every kind.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.services.ai.backend import BackendLease
from app.services.ai.providers.credentials import CloudCredentials
from app.services.ai.providers.tokens import CredentialUnavailable
from app.services.ai.upstream import UpstreamCredentialsUnavailable


class UpstreamCall(BaseModel):
    """Where one request goes, its headers, and its body."""

    model_config = ConfigDict(frozen=True)

    url: str
    headers: dict[str, str]
    body: dict[str, Any]


class ProviderCalls:
    """Addresses and signs calls to backends, with the credentials of the app it serves."""

    def __init__(self, credentials: CloudCredentials) -> None:
        self._credentials = credentials

    async def prepare(self, lease: BackendLease, *, body: dict[str, Any]) -> UpstreamCall:
        """The call for *body* to the backend behind *lease*.

        The model is renamed to the provider's own name for it here, at the
        last moment, so everything before — billing, stickiness, the
        caller's view — keeps the name the caller asked for.
        """
        if 'model' in body:
            body = {**body, 'model': lease.upstream_model(body['model'])}
        return UpstreamCall(url=lease.completions_url, headers=await self._headers(lease), body=body)

    def forget(self, lease: BackendLease) -> None:
        """Drop the credential the provider just refused."""
        self._credentials.invalidate(lease.signer)

    async def _headers(self, lease: BackendLease) -> dict[str, str]:
        if not lease.renews_token:
            return lease.auth_headers()
        try:
            token = await self._credentials.bearer(lease.signer)
        except CredentialUnavailable as exc:
            raise UpstreamCredentialsUnavailable(exc.kind) from exc
        return {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'}
