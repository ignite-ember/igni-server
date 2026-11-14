"""Anthropic's Messages API, as `POST /v1/messages` takes and answers it.

The request is passed on as the caller wrote it: the model is the only
field this server reads to route, and everything else — system prompt,
tools, thinking, cache control, images, PDFs — goes to the provider
untouched. That is the reason for a native endpoint at all: Claude's
features do not survive a translation into the OpenAI shape.

A refusal is Anthropic's error envelope, ``{"type": "error", "error":
{"type", "message"}}``, so an Anthropic SDK raises the exception class
its caller expects and reads the sentence this server wrote.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MessagesRequest(BaseModel):
    """A Messages API request. Unknown fields pass through to the provider."""

    model_config = ConfigDict(extra='allow')

    model: str = Field(min_length=1)
    messages: list[dict[str, Any]] = Field(min_length=1, max_length=10_000)
    max_tokens: int = Field(gt=0, le=1_000_000)
    stream: bool | None = None

    def wire(self) -> dict[str, Any]:
        """The body as the caller sent it, for the provider."""
        return self.model_dump(exclude_none=True)


#: Status → the error ``type`` Anthropic's SDKs map to an exception class.
_TYPES = {
    400: 'invalid_request_error',
    401: 'authentication_error',
    403: 'permission_error',
    404: 'not_found_error',
    413: 'request_too_large',
    429: 'rate_limit_error',
    503: 'overloaded_error',
    529: 'overloaded_error',
}


def error_type(status_code: int) -> str:
    """The Anthropic error type for *status_code*."""
    if status_code in _TYPES:
        return _TYPES[status_code]
    return 'api_error' if status_code >= 500 else 'invalid_request_error'


class AnthropicError(BaseModel):
    type: str
    message: str


class AnthropicErrorBody(BaseModel):
    """The whole refusal body, as an Anthropic SDK parses it."""

    type: str = 'error'
    error: AnthropicError

    @classmethod
    def of(cls, *, message: str, status_code: int) -> AnthropicErrorBody:
        return cls(error=AnthropicError(type=error_type(status_code), message=message))

    @classmethod
    def sse_frame(cls, *, message: str, status_code: int) -> str:
        """The refusal as the `error` event of a stream that fails after its 200."""
        body = cls.of(message=message, status_code=status_code)
        return f'event: error\ndata: {json.dumps(body.model_dump())}\n\n'
