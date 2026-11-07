"""Why a chat request was refused, in terms the router turns into HTTP.

Services raise these and never `HTTPException`; the router translates
`ChatRefused` in one place.
"""

from __future__ import annotations

from fastapi import status

from app.services.ai.upstream import UpstreamFailure

#: Seconds a caller refused for capacity is told to wait.
RETRY_AFTER_SECONDS = 30


class ChatRefused(Exception):
    """A request this server will not serve, with the status and sentence to answer with."""

    def __init__(self, *, status_code: int, detail: str, retry_after: int | None = None) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.retry_after = retry_after


class QueueFullRefusal(ChatRefused):
    """Every node is full and so is the queue."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail='Every inference node is at capacity and the queue is full. Please retry shortly.',
            retry_after=RETRY_AFTER_SECONDS,
        )


class QueueTimeoutRefusal(ChatRefused):
    """Waited the longest a request may for a node."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail='Waited the longest a request may for a free inference node. Please retry.',
            retry_after=RETRY_AFTER_SECONDS,
        )


class ModelUnavailableRefusal(ChatRefused):
    """No node serves the model asked for."""

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class BackendMisconfiguredRefusal(ChatRefused):
    """No node configured, or a configuration that does not parse: a 503, since waiting will not help."""

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


class UpstreamRefusal(ChatRefused):
    """The nodes tried did not produce a usable answer; the failure says which way."""

    def __init__(self, failure: UpstreamFailure) -> None:
        answer = failure.answer_status
        super().__init__(
            status_code=answer,
            detail=failure.sentence,
            retry_after=RETRY_AFTER_SECONDS if answer == status.HTTP_429_TOO_MANY_REQUESTS else None,
        )
        self.failure = failure


class NoOtherNode(Exception):
    """A failover could not take another node."""
