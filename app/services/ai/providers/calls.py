"""One request to a backend, addressed and signed the way its kind needs.

The chat client hands over the lease and the body; this says where it
goes and with which headers. Everything after that — the timeouts, the
stream, the status handling — is the same for every kind.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.ai.providers.credentials import CloudCredentials, CredentialUnavailable
from app.services.ai.providers.kinds import BackendKind
from app.services.ai.schemas import BackendLease
from app.services.ai.upstream import UpstreamCredentialsUnavailable


@dataclass(frozen=True)
class UpstreamCall:
    """Where one request goes, its headers, and its body."""

    url: str
    headers: dict[str, str]
    body: dict[str, Any]


async def prepare(lease: BackendLease, body: dict[str, Any], credentials: CloudCredentials) -> UpstreamCall:
    """The call for *body* to the backend behind *lease*.

    The model is renamed to the provider's own name for it here, at the
    last moment, so everything before — billing, stickiness, the caller's
    view — keeps the name the caller asked for.
    """
    if 'model' in body:
        body = {**body, 'model': lease.upstream_model(body['model'])}
    return UpstreamCall(url=lease.completions_url, headers=await _headers(lease, credentials), body=body)


def renews(lease: BackendLease) -> bool:
    """Whether a 401 or 403 from this backend may be a token that needs renewing.

    Only for a token this server obtained. A static key refused is
    refused for good, and asking again would only repeat it.
    """
    if lease.kind is BackendKind.AZURE_OPENAI:
        return not lease.api_key
    return lease.kind.is_cloud


def forget_token(lease: BackendLease, credentials: CloudCredentials) -> None:
    """Drop the token the provider just refused."""
    credentials.invalidate(backend_id=lease.backend_id, kind=lease.kind, credential=lease.credential)


async def _headers(lease: BackendLease, credentials: CloudCredentials) -> dict[str, str]:
    if lease.kind is BackendKind.OPENAI:
        return lease.auth_headers()
    if lease.kind is BackendKind.AZURE_OPENAI and lease.api_key:
        # The resource's own key, in Azure's own header.
        return {'Content-Type': 'application/json', 'api-key': lease.api_key}
    try:
        token = await credentials.bearer(backend_id=lease.backend_id, kind=lease.kind, credential=lease.credential)
    except CredentialUnavailable as exc:
        raise UpstreamCredentialsUnavailable(exc.kind.value) from exc
    return {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'}
