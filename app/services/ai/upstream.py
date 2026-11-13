"""What went wrong with an inference node, and whether to ask another.

Every call this server makes to a GPU meets the same failures: no
answer, an answer that is an error, an answer that is unparseable, and
an answer that stops partway. Each is an `UpstreamFailure` subclass, and
each says for itself whether another node could fix it and what an
HTTP caller should be told, so the streaming and non-streaming paths
cannot disagree about either.

This module is deliberately HTTP-free. `answer_status` is a number a
caller may use; turning it into a response is the caller's job.
"""

from __future__ import annotations

import asyncio
import re

import httpx
from pydantic import BaseModel, ConfigDict

#: Attempts per request, including the first.
MAX_RETRIES = 3

#: Seconds before attempt *n*. A retry normally lands on a *different*
#: node, so the pause is for the pool with one node, or one that hands
#: the same node back. Short, because somebody is waiting.
RETRY_BACKOFF = 0.25

_NO_ANSWER = 'No inference node answered. Try again shortly.'
_STOPPED = 'The inference node stopped responding partway through.'


class UpstreamFailure(Exception):
    """A node did not produce a usable answer. The subclass is the diagnosis."""

    @property
    def label(self) -> str:
        """A short name for the log line."""
        return 'failure'

    @property
    def answer_status(self) -> int:
        """The status an HTTP caller should answer with, once retrying is over."""
        return 502

    @property
    def sentence(self) -> str:
        """What the caller is told. Each failure has its own, because each has its own fix."""
        return _NO_ANSWER

    @property
    def worth_another_node(self) -> bool:
        """Whether a different node could plausibly answer.

        A property of the node that happened to be picked — no answer, a
        5xx, a silent stream, nonsense — is what another node fixes.
        """
        return True

    @classmethod
    def from_transport(cls, exc: httpx.RequestError) -> UpstreamFailure:
        """The failure for an `httpx` error raised before any usable reply."""
        if isinstance(exc, httpx.TimeoutException):
            return UpstreamTimeout(exc)
        return UpstreamUnreachable(exc)


class UpstreamStale(UpstreamFailure):
    """The node stopped sending data for too long, mid-answer."""

    @property
    def label(self) -> str:
        return 'stale'

    @property
    def answer_status(self) -> int:
        return 504

    @property
    def sentence(self) -> str:
        return _STOPPED


class UpstreamError(UpstreamFailure):
    """The node answered with a non-200 status."""

    #: `body` is kept for logging only — the streaming path reads it as
    #: bytes, the non-streaming one as text. Never put on the wire: the
    #: body of a failed completion can carry the prompt back.
    def __init__(self, status_code: int, body: bytes | str):
        self.status_code = status_code
        self.body = body
        super().__init__(f'Upstream returned {status_code}')

    @property
    def label(self) -> str:
        return f'{self.status_code}'

    @property
    def answer_status(self) -> int:
        """A 4xx passes through: the caller asked for something this model will not do."""
        if self.status_code == 429 or 400 <= self.status_code < 500:
            return self.status_code
        return 502

    @property
    def sentence(self) -> str:
        if self.status_code == 429:
            return 'Every inference node is at capacity. Please retry shortly.'
        if 400 <= self.status_code < 500:
            return f'The inference node refused the request ({self.status_code}).'
        return f'The inference node returned {self.status_code}.'

    @property
    def worth_another_node(self) -> bool:
        """A 4xx is about the request, which is the same on every node; 408 and 429 are about this node's moment."""
        return self.status_code in (408, 429) or self.status_code >= 500


class UpstreamUnreadable(UpstreamFailure):
    """A 200 carrying something that is not the response we asked for.

    No status to reason about: the node said all was well and sent
    nonsense, which is a broken node and always worth asking another.
    """

    def __init__(self, excerpt: str):
        self.excerpt = excerpt
        super().__init__('Upstream returned an unreadable body')

    @property
    def label(self) -> str:
        return 'unreadable'

    @property
    def sentence(self) -> str:
        return 'The inference node returned an unreadable response.'


class _TransportFailure(UpstreamFailure):
    """No usable reply at the transport level; the `httpx` error is the cause."""

    def __init__(self, cause: httpx.RequestError):
        self.cause = cause
        super().__init__(str(cause))

    @property
    def label(self) -> str:
        """The cause's name in snake case: `ReadTimeout` is `read_timeout`."""
        return re.sub(r'(?<!^)(?=[A-Z])', '_', type(self.cause).__name__).lower()


class UpstreamTimeout(_TransportFailure):
    """The node took the request and then went quiet."""

    @property
    def answer_status(self) -> int:
        return 504

    @property
    def sentence(self) -> str:
        return _STOPPED


class UpstreamUnreachable(_TransportFailure):
    """No node answered at all: refused, reset, or unresolvable."""


class UpstreamCredentialsUnavailable(UpstreamFailure):
    """The server could not get a token for a cloud backend, so nothing was sent.

    Another backend may run under a different identity, so it is worth
    asking one; once retrying is over the caller is told the service is
    unavailable rather than that a node failed, because the fix is the
    server's configuration, not the provider.
    """

    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(f'No credentials for the {kind} backend')

    @property
    def label(self) -> str:
        return 'credentials'

    @property
    def answer_status(self) -> int:
        return 503

    @property
    def sentence(self) -> str:
        return 'The server could not obtain credentials for the model provider.'


class RetryPolicy(BaseModel):
    """How many nodes one request may try, and how long to pause between them."""

    model_config = ConfigDict(frozen=True)

    max_retries: int = MAX_RETRIES
    backoff: float = RETRY_BACKOFF

    def gives_up(self, *, attempt: int, failure: UpstreamFailure) -> bool:
        """Out of attempts, or nothing another node would fix."""
        return attempt >= self.max_retries or not failure.worth_another_node

    async def pause(self, attempt: int) -> None:
        """Wait before attempt *attempt* + 1."""
        await asyncio.sleep(self.backoff * attempt)
