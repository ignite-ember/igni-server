"""The error envelope an OpenAI SDK parses, for both transports.

A refusal on ``/chat/completions`` arrives as an HTTP body; a stream that
fails after its 200 sends the same envelope as an SSE frame. One shape,
defined here, so the two cannot drift apart.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

#: Status → the ``type`` OpenAI puts on the error. Clients branch on
#: this (``openai.AuthenticationError`` vs ``RateLimitError`` vs
#: ``APIStatusError``), so an approximate mapping produces the wrong
#: exception class and the wrong retry behaviour.
_TYPES = {
    400: 'invalid_request_error',
    401: 'authentication_error',
    403: 'permission_error',
    404: 'invalid_request_error',
    409: 'invalid_request_error',
    413: 'invalid_request_error',
    429: 'rate_limit_error',
}


class OpenAIError(BaseModel):
    """The `error` object an OpenAI SDK parses; four keys fixed by somebody else's published API."""

    message: str
    type: str
    #: Null rather than invented. A client that branches on a code
    #: wants the provider's own vocabulary, and this server does not
    #: have one to offer yet.
    code: str | None = None
    param: str | None = None


class OpenAIErrorBody(BaseModel):
    """The whole response body: the envelope, plus FastAPI's own `detail`.

    `detail` is kept so nothing that reads it today has to change. It is
    `Any` because it is whatever was raised — the one field here with no
    knowable shape.
    """

    error: OpenAIError
    detail: Any = None

    @classmethod
    def sse_frame(cls, *, message: str, status_code: int) -> str:
        """The envelope as one SSE frame, for a stream that fails after its 200.

        `detail` is excluded by name rather than with `exclude_none`:
        `code` and `param` are null on purpose, and an SDK reads them.
        """
        envelope = cls(error=error_envelope(message=message, status_code=status_code))
        return f'data: {json.dumps(envelope.model_dump(exclude={"detail"}))}\n\n'


def _type_for(*, status_code: int) -> str:
    if status_code in _TYPES:
        return _TYPES[status_code]
    return 'server_error' if status_code >= 500 else 'invalid_request_error'


def error_envelope(*, message: str, status_code: int) -> OpenAIError:
    """The ``error`` object for a status, shared by both transports."""
    return OpenAIError(message=message, type=_type_for(status_code=status_code))
