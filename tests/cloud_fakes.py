"""Fakes for the cloud side of the inference backends: token sources that never reach a cloud.

`FakeCloud` stands in for every cloud SDK at once: each kind and method
gets a `TokenSource` that records what it was built from and hands out
``tok-1``, ``tok-2``, ... — so a test can say exactly which credential a
request was signed with and how often the cloud was asked.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from app.services.ai.backend import BackendLease, InferenceBackend
from app.services.ai.providers.auth import AuthMethod, METHODS
from app.services.ai.providers.aws import AwsKeys
from app.services.ai.providers.credentials import CloudCredentials
from app.services.ai.providers.kinds import BackendKind
from app.services.ai.providers.tokens import AccessToken, TokenSource

#: The keys every fake Bedrock source hands out; the secret is easy to spot if it leaks.
AWS_KEYS = AwsKeys(access_key_id='AKIDEXAMPLE', secret_access_key='SECRET-MATERIAL', session_token='SESSION')

#: A service-account key whose secret part is easy to spot if it leaks.
SERVICE_ACCOUNT = '{"type": "service_account", "private_key": "PRIVATE-KEY-MATERIAL", "client_email": "x"}'

AZURE = {'kind': 'azure_openai', 'base_url': 'https://res.openai.azure.com/openai/v1'}
VERTEX = {'kind': 'vertex_openai', 'project': 'p1', 'region': 'europe-west4'}


def backend(**fields: object) -> InferenceBackend:
    """A validated backend serving model ``m``, with *fields* on top."""
    return InferenceBackend.validated(**{'id': 'b', 'models': ['m'], **fields})


def lease_of(
    kind: BackendKind = BackendKind.OPENAI, auth: AuthMethod = AuthMethod.NONE, **fields: object
) -> BackendLease:
    """A lease on a backend at ``http://node.invalid/v1`` for model ``public``."""
    return BackendLease.model_validate(
        {
            'lease_id': 'l',
            'backend_id': 'b',
            'base_url': 'http://node.invalid/v1',
            'model': 'public',
            'kind': kind,
            'auth': auth,
            **fields,
        }
    )


class FakeCloud:
    """Every cloud at once: records each fetch, and fails every one when told to."""

    def __init__(
        self, *, fail: bool = False, lifetime: float = 3600.0, clock: Callable[[], float] = lambda: 1000.0
    ) -> None:
        self.fetches: list[tuple[BackendKind, AuthMethod, dict[str, str], str | None]] = []
        self.fail = fail
        self.lifetime = lifetime
        self.clock = clock

    def source(self, kind: BackendKind, method: AuthMethod) -> type[TokenSource[str]]:
        """The token source for *kind* and *method*, reporting to this cloud."""
        cloud = self

        class Source(TokenSource[str]):
            async def fetch(self) -> AccessToken[str]:
                cloud.fetches.append((kind, method, dict(self._settings), self._secret))
                if cloud.fail:
                    raise RuntimeError('no identity here')
                return AccessToken(value=f'tok-{len(cloud.fetches)}', expires_at=cloud.clock() + cloud.lifetime)

        return Source

    def key_source(self, method: AuthMethod) -> type[TokenSource[AwsKeys]]:
        """Bedrock's key source for *method*: `AWS_KEYS`, recorded like a token fetch."""
        cloud = self

        class Source(TokenSource[AwsKeys]):
            async def fetch(self) -> AccessToken[AwsKeys]:
                cloud.fetches.append((BackendKind.BEDROCK, method, dict(self._settings), self._secret))
                if cloud.fail:
                    raise RuntimeError('no identity here')
                return AccessToken(value=AWS_KEYS, expires_at=cloud.clock() + cloud.lifetime)

        return Source

    def credentials(self) -> CloudCredentials:
        """A credentials cache whose every source is this cloud's."""
        return CloudCredentials(
            sources={
                (kind, method): self.source(kind, method)
                for kind, methods in METHODS.items()
                for method in methods
                if method.is_token and kind is not BackendKind.BEDROCK
            },
            key_sources={
                (BackendKind.BEDROCK, method): self.key_source(method) for method in METHODS[BackendKind.BEDROCK]
            },
            clock=self.clock,
        )


class ScriptedProvider:
    """A provider behind a mock transport: answers each request in turn from *answers*, the last from then on.

    It records every request it was sent, so a test says exactly what reached it.
    """

    def __init__(self, *answers: httpx.Response) -> None:
        self.answers = answers
        self.sent: list[httpx.Request] = []

    @classmethod
    def refusing(cls, times: int, then: httpx.Response, *, status: int = 401) -> ScriptedProvider:
        """Refuses the first *times* requests with *status*, then answers *then*."""
        return cls(*(httpx.Response(status, text='refused') for _ in range(times)), then)

    def __call__(self, **kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._answer), **kwargs)

    def authorizations(self) -> list[str | None]:
        """The `authorization` header of each request, in order."""
        return [request.headers.get('authorization') for request in self.sent]

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        return self.answers[min(len(self.sent), len(self.answers)) - 1]
