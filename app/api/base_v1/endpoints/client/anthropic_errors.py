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

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.api.base_v1.schema.messages import AnthropicErrorBody


def anthropic_error(exc: HTTPException) -> JSONResponse:
    """One refusal, Anthropic-shaped."""
    message = exc.detail if isinstance(exc.detail, str) else str(exc.detail or '')
    return JSONResponse(
        status_code=exc.status_code,
        content=AnthropicErrorBody.of(message=message, status_code=exc.status_code).model_dump(),
        headers=exc.headers,
    )


class AnthropicCompatibleRoute(APIRoute):
    """A route whose ``HTTPException``\\ s come back Anthropic-shaped, dependencies' included."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def answer_in_anthropic_shape(request: Request) -> Response:
            try:
                return await original(request)
            except HTTPException as exc:
                return anthropic_error(exc)

        return answer_in_anthropic_shape
