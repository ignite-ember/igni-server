"""Errors on the OpenAI-compatible surface, in the shape OpenAI clients read.

``/chat/completions`` advertises itself as OpenAI-compatible, and every
caller reaches it through an OpenAI SDK. Those SDKs parse a failure as
``{"error": {"message": ...}}`` — that envelope is the only place they
look for a reason. FastAPI's default is ``{"detail": ...}``, so every
refusal this endpoint made arrived at the caller as a reason-shaped
hole, and the client filled it with a generic string of its own.

What that cost, measured: a node went away, this server answered
502 ``No inference node answered. Try again shortly.`` — an accurate,
actionable sentence — and the screen said **"Unknown model error"**.
That is the openai SDK's ``.get("message", "Unknown model error")``
finding no ``error`` key, and then Agno putting the resulting string
where the agent's answer goes. The sentence was written, sent, and
thrown away one layer from the person who needed it.

So the shape is fixed here rather than at each ``raise``: a route class
converts, which covers the handler, every dependency it runs (an
expired token is a 401 nobody could read either), and anything added
later without remembering this file exists.

**``detail`` is kept alongside.** It costs one key and it means the
curl-and-eyeballs path, the portal, and the existing tests still read
the same thing they always did. An OpenAI SDK ignores what it does not
know, so nothing is confused by it.

Validation errors (422) are deliberately **not** converted: FastAPI's
``detail`` list says which field and why, per field, which is more than
``error.param`` can carry, and no SDK sends a body it did not build.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.api.base_v1.schema.openai_error import error_envelope, OpenAIErrorBody


def _message_of(detail: Any) -> str:
    """The sentence to show, out of whatever was raised.

    Almost always a string. A dict or a list is a structured detail
    that has no single sentence in it, so it is rendered rather than
    dropped — an unreadable message beats an empty one, and the
    original is still under ``detail``.
    """
    if isinstance(detail, str):
        return detail
    return str(detail) if detail is not None else ''


def openai_error(exc: HTTPException) -> JSONResponse:
    """One refusal, in both shapes."""
    return JSONResponse(
        status_code=exc.status_code,
        content=OpenAIErrorBody(
            error=error_envelope(message=_message_of(exc.detail), status_code=exc.status_code),
            detail=exc.detail,
        ).model_dump(),
        headers=exc.headers,
    )


class OpenAICompatibleRoute(APIRoute):
    """A route whose ``HTTPException``\\ s come back OpenAI-shaped.

    Wrapping the route handler rather than registering an app-level
    handler is what catches **dependency** failures too: FastAPI runs
    the dependency tree inside this handler, so the 401 from a bad
    token lands here, which is the error an OpenAI SDK is most likely
    to meet and least able to read.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def answer_in_openai_shape(request: Request) -> Response:
            try:
                return await original(request)
            except HTTPException as exc:
                return openai_error(exc)

        return answer_in_openai_shape
