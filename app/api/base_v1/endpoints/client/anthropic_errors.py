"""Errors on `/v1/messages`, in the shape Anthropic's SDKs read.

The same reasoning as `openai_errors.py`, for the other SDK: a refusal
in FastAPI's ``{"detail": ...}`` reaches an Anthropic client as a reason-
shaped hole. A route class converts every ``HTTPException`` — the
handler's and its dependencies', an expired token among them — into
``{"type": "error", "error": {"type", "message"}}``.

Validation errors (422) are left as FastAPI writes them, for the reason
``openai_errors.py`` gives.
"""

from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.api.base_v1.endpoints.client.shaped_errors import message_of, ShapedErrorRoute
from app.api.base_v1.schema.messages import AnthropicErrorBody


def anthropic_error(exc: HTTPException) -> JSONResponse:
    """One refusal, Anthropic-shaped."""
    return JSONResponse(
        status_code=exc.status_code,
        content=AnthropicErrorBody.of(message=message_of(exc.detail), status_code=exc.status_code).model_dump(),
        headers=exc.headers,
    )


class AnthropicCompatibleRoute(ShapedErrorRoute):
    """A route whose ``HTTPException``\\ s come back Anthropic-shaped, dependencies' included."""

    render = staticmethod(anthropic_error)
