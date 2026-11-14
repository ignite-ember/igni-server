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

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.api.base_v1.endpoints.client.shaped_errors import message_of, ShapedErrorRoute
from app.api.base_v1.schema.openai_error import error_envelope, OpenAIErrorBody


def openai_error(exc: HTTPException) -> JSONResponse:
    """One refusal, in both shapes."""
    return JSONResponse(
        status_code=exc.status_code,
        content=OpenAIErrorBody(
            error=error_envelope(message=message_of(exc.detail), status_code=exc.status_code),
            detail=exc.detail,
        ).model_dump(),
        headers=exc.headers,
    )


class OpenAICompatibleRoute(ShapedErrorRoute):
    """A route whose ``HTTPException``\\ s come back OpenAI-shaped, dependencies' included.

    The 401 from a bad token is the error an OpenAI SDK is most likely to
    meet and least able to read; `ShapedErrorRoute` catches it.
    """

    render = staticmethod(openai_error)
