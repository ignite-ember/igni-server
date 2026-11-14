"""One Messages request to a backend, addressed, versioned and signed the way its kind needs.

Each Claude kind has its own call, chosen once by kind (`MessagesCalls`):

* ``anthropic``, ``azure_anthropic`` — `AnthropicApiCall`: ``{base}/v1/messages``;
  the version in the ``anthropic-version`` header; ``x-api-key``, or an Entra ``Bearer``.
* ``vertex_anthropic`` — `VertexClaudeCall`: ``{base}/{model}:rawPredict``
  (``:streamRawPredict``); ``anthropic_version`` in the body; a Google ``Bearer``.
* ``bedrock`` — `BedrockCall`: ``{base}/model/{model}/invoke``
  (``invoke-with-response-stream``); ``anthropic_version`` and betas in the body; SigV4.

Vertex and Bedrock name the model in the address, not the body.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import json
from types import MappingProxyType
from typing import Any
from urllib.parse import quote

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from pydantic import BaseModel, ConfigDict

from app.services.ai.backend import BackendLease
from app.services.ai.providers.auth import AuthMethod
from app.services.ai.providers.aws import AwsKeys
from app.services.ai.providers.credentials import CloudCredentials
from app.services.ai.providers.kinds import BackendKind
from app.services.ai.providers.tokens import CredentialUnavailable
from app.services.ai.upstream import UpstreamCredentialsUnavailable

#: The Messages API version sent when the caller names none.
ANTHROPIC_VERSION = '2023-06-01'
#: The body's `anthropic_version` each cloud requires.
BEDROCK_VERSION = 'bedrock-2023-05-31'
VERTEX_VERSION = 'vertex-2023-10-16'


class MessagesCall(BaseModel):
    """Where one request goes, its headers, and its body as the bytes that were signed."""

    model_config = ConfigDict(frozen=True)

    url: str
    headers: dict[str, str]
    content: bytes


class CallerHeaders(BaseModel):
    """What the caller asked for in its headers: the API version and any beta features."""

    model_config = ConfigDict(frozen=True)

    version: str | None = None
    betas: tuple[str, ...] = ()


def sigv4(*, keys: AwsKeys, region: str, url: str, headers: dict[str, str], content: bytes) -> dict[str, str]:
    """*headers* with Bedrock's SigV4 signature added, over exactly *content*."""
    request = AWSRequest(method='POST', url=url, data=content, headers=dict(headers))
    credentials = Credentials(keys.access_key_id, keys.secret_access_key, keys.session_token)
    SigV4Auth(credentials, 'bedrock', region).add_auth(request)
    return dict(request.headers.items())


class ClaudeCall(ABC):
    """How one Claude kind is called: its address, its version, its signature."""

    def __init__(self, credentials: CloudCredentials) -> None:
        self._credentials = credentials

    @abstractmethod
    async def prepare(
        self, lease: BackendLease, *, body: dict[str, Any], stream: bool, asked: CallerHeaders
    ) -> MessagesCall:
        """The call for *body* to the backend behind *lease*."""

    async def _bearer(self, lease: BackendLease) -> str:
        try:
            return await self._credentials.bearer(lease.signer)
        except CredentialUnavailable as exc:
            raise UpstreamCredentialsUnavailable(exc.kind) from exc


class AnthropicApiCall(ClaudeCall):
    """Anthropic's own API, a compatible gateway, or Claude in Microsoft Foundry."""

    async def prepare(
        self, lease: BackendLease, *, body: dict[str, Any], stream: bool, asked: CallerHeaders
    ) -> MessagesCall:
        headers = {'content-type': 'application/json', 'anthropic-version': asked.version or ANTHROPIC_VERSION}
        if asked.betas:
            headers['anthropic-beta'] = ','.join(asked.betas)
        if lease.auth is AuthMethod.API_KEY:
            headers['x-api-key'] = lease.api_key or ''
        else:
            headers['authorization'] = f'Bearer {await self._bearer(lease)}'
        payload = {**body, 'model': lease.upstream_model(str(body['model']))}
        url = f'{lease.base_url.rstrip("/")}/v1/messages'
        return MessagesCall(url=url, headers=headers, content=json.dumps(payload).encode())


class VertexClaudeCall(ClaudeCall):
    """Claude on Vertex AI: the model in the address, the version in the body."""

    async def prepare(
        self, lease: BackendLease, *, body: dict[str, Any], stream: bool, asked: CallerHeaders
    ) -> MessagesCall:
        headers = {'content-type': 'application/json', 'authorization': f'Bearer {await self._bearer(lease)}'}
        if asked.betas:
            headers['anthropic-beta'] = ','.join(asked.betas)
        payload = {key: value for key, value in body.items() if key != 'model'}
        payload['anthropic_version'] = VERTEX_VERSION
        model = quote(lease.upstream_model(str(body['model'])), safe='@')
        action = 'streamRawPredict' if stream else 'rawPredict'
        url = f'{lease.base_url.rstrip("/")}/{model}:{action}'
        return MessagesCall(url=url, headers=headers, content=json.dumps(payload).encode())


class BedrockCall(ClaudeCall):
    """Claude on Bedrock: the model in the address, version and betas in the body, signed with SigV4."""

    async def prepare(
        self, lease: BackendLease, *, body: dict[str, Any], stream: bool, asked: CallerHeaders
    ) -> MessagesCall:
        payload = {key: value for key, value in body.items() if key not in ('model', 'stream')}
        payload['anthropic_version'] = BEDROCK_VERSION
        if asked.betas:
            payload['anthropic_beta'] = list(asked.betas)
        # URL-encoded, so an inference profile's ARN is one path segment.
        model = quote(lease.upstream_model(str(body['model'])), safe='')
        action = 'invoke-with-response-stream' if stream else 'invoke'
        url = f'{lease.base_url.rstrip("/")}/model/{model}/{action}'
        content = json.dumps(payload).encode()
        headers = {
            'content-type': 'application/json',
            'accept': 'application/vnd.amazon.eventstream' if stream else 'application/json',
        }
        try:
            keys = await self._credentials.aws(lease.signer)
        except CredentialUnavailable as exc:
            raise UpstreamCredentialsUnavailable(exc.kind) from exc
        signed = sigv4(keys=keys, region=lease.region or '', url=url, headers=headers, content=content)
        return MessagesCall(url=url, headers=signed, content=content)


#: The call for each Claude kind.
CALLS: MappingProxyType[BackendKind, type[ClaudeCall]] = MappingProxyType(
    {
        BackendKind.ANTHROPIC: AnthropicApiCall,
        BackendKind.AZURE_ANTHROPIC: AnthropicApiCall,
        BackendKind.VERTEX_ANTHROPIC: VertexClaudeCall,
        BackendKind.BEDROCK: BedrockCall,
    }
)


class MessagesCalls:
    """Addresses and signs Messages calls, with the credentials of the app it serves."""

    def __init__(self, credentials: CloudCredentials) -> None:
        self._credentials = credentials
        self._calls = {kind: call(credentials) for kind, call in CALLS.items()}

    async def prepare(
        self, lease: BackendLease, *, body: dict[str, Any], stream: bool, asked: CallerHeaders
    ) -> MessagesCall:
        """The call for *body* to the backend behind *lease*, as its kind makes it."""
        return await self._calls[lease.kind].prepare(lease, body=body, stream=stream, asked=asked)

    def forget(self, lease: BackendLease) -> None:
        """Drop the credential the provider just refused."""
        self._credentials.invalidate(lease.signer)
