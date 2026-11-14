"""A route whose refusals come back in the shape its callers' SDK reads.

`/chat/completions` answers in OpenAI's error envelope and `/v1/messages` in
Anthropic's; each is a `ShapedErrorRoute` that only says how to render one.
Wrapping the route handler, rather than registering an app-level handler,
is what catches dependency failures too: FastAPI runs the dependency tree
inside the handler, so an expired token's 401 lands here as well.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


def message_of(detail: Any) -> str:
    """The sentence to show, out of whatever was raised.

    Almost always a string. A dict or a list is a structured detail that has
    no single sentence in it, so it is rendered rather than dropped — an
    unreadable message beats an empty one.
    """
    if isinstance(detail, str):
        return detail
    return str(detail) if detail is not None else ''


class ShapedErrorRoute(APIRoute):
    """A route whose ``HTTPException``\\ s, dependencies' included, are rendered by `render`."""

    @staticmethod
    @abstractmethod
    def render(exc: HTTPException) -> JSONResponse:
        """One refusal, in this route's shape."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()
        render = self.render

        async def answer_in_shape(request: Request) -> Response:
            try:
                return await original(request)
            except HTTPException as exc:
                return render(exc)

        return answer_in_shape
