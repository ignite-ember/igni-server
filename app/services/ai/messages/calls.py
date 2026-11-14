"""One Messages request to a backend, addressed, versioned and signed the way its kind needs.

* ``anthropic``, ``azure_anthropic`` — ``{base}/v1/messages``; the version
  in the ``anthropic-version`` header; ``x-api-key``, or an Entra ``Bearer``.
* ``vertex_anthropic`` — ``{base}/{model}:rawPredict`` (``:streamRawPredict``);
  ``anthropic_version`` in the body; a Google ``Bearer``.
* ``bedrock`` — ``{base}/model/{model}/invoke`` (``invoke-with-response-stream``);
  ``anthropic_version`` in the body; SigV4.

Vertex and Bedrock name the model in the address, not the body; Bedrock
takes beta flags in the body and the stream from the address.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
from urllib.parse import quote

from app.services.ai.providers.auth import AuthMethod
from app.services.ai.providers.calls import signer_of
from app.services.ai.providers.credentials import AwsKeys, CloudCredentials, CredentialUnavailable
from app.services.ai.providers.kinds import BackendKind
from app.services.ai.schemas import BackendLease
from app.services.ai.upstream import UpstreamCredentialsUnavailable

#: The Messages API version sent when the caller names none.
ANTHROPIC_VERSION = '2023-06-01'
#: The body's `anthropic_version` each cloud requires.
BEDROCK_VERSION = 'bedrock-2023-05-31'
VERTEX_VERSION = 'vertex-2023-10-16'


@dataclass(frozen=True)
class MessagesCall:
    """Where one request goes, its headers, and its body as the bytes that were signed."""

    url: str
    headers: dict[str, str]
    content: bytes


@dataclass(frozen=True)
class CallerHeaders:
    """What the caller asked for in its headers: the API version and any beta features."""

    version: str | None = None
    betas: tuple[str, ...] = ()


async def prepare(
    lease: BackendLease,
    body: dict[str, Any],
    *,
    stream: bool,
    asked: CallerHeaders,
    credentials: CloudCredentials,
) -> MessagesCall:
    """The call for *body* to the backend behind *lease*."""
    model = lease.upstream_model(str(body['model']))
    base = lease.base_url.rstrip('/')
    headers = {'content-type': 'application/json'}

    if lease.kind is BackendKind.BEDROCK:
        payload = {k: v for k, v in body.items() if k not in ('model', 'stream')}
        payload['anthropic_version'] = BEDROCK_VERSION
        if asked.betas:
            payload['anthropic_beta'] = list(asked.betas)
        action = 'invoke-with-response-stream' if stream else 'invoke'
        url = f'{base}/model/{quote(model, safe="")}/{action}'
        content = json.dumps(payload).encode()
        headers['accept'] = 'application/vnd.amazon.eventstream' if stream else 'application/json'
        keys = await _aws_keys(lease, credentials)
        return MessagesCall(url=url, headers=sigv4(keys, lease.region or '', url, headers, content), content=content)

    if asked.betas:
        headers['anthropic-beta'] = ','.join(asked.betas)

    if lease.kind is BackendKind.VERTEX_ANTHROPIC:
        payload = {k: v for k, v in body.items() if k != 'model'}
        payload['anthropic_version'] = VERTEX_VERSION
        url = f'{base}/{quote(model, safe="@")}:{"streamRawPredict" if stream else "rawPredict"}'
        headers['authorization'] = f'Bearer {await _bearer(lease, credentials)}'
        return MessagesCall(url=url, headers=headers, content=json.dumps(payload).encode())

    # Anthropic's own API, a compatible gateway, or Claude in Microsoft Foundry.
    headers['anthropic-version'] = asked.version or ANTHROPIC_VERSION
    if lease.auth is AuthMethod.API_KEY:
        headers['x-api-key'] = lease.api_key or ''
    else:
        headers['authorization'] = f'Bearer {await _bearer(lease, credentials)}'
    payload = {**body, 'model': model}
    return MessagesCall(url=f'{base}/v1/messages', headers=headers, content=json.dumps(payload).encode())


def sigv4(keys: AwsKeys, region: str, url: str, headers: dict[str, str], content: bytes) -> dict[str, str]:
    """*headers* with Bedrock's SigV4 signature added, over exactly *content*."""
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials

    request = AWSRequest(method='POST', url=url, data=content, headers=dict(headers))
    SigV4Auth(Credentials(keys.access_key_id, keys.secret_access_key, keys.session_token), 'bedrock', region).add_auth(
        request
    )
    return dict(request.headers.items())


async def _bearer(lease: BackendLease, credentials: CloudCredentials) -> str:
    try:
        return await credentials.bearer(signer_of(lease))
    except CredentialUnavailable as exc:
        raise UpstreamCredentialsUnavailable(exc.kind.value) from exc


async def _aws_keys(lease: BackendLease, credentials: CloudCredentials) -> AwsKeys:
    try:
        return await credentials.aws(signer_of(lease))
    except CredentialUnavailable as exc:
        raise UpstreamCredentialsUnavailable(exc.kind.value) from exc
