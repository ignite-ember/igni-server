"""The chat proxy's answer when it refuses a request.

The chat services raise `ChatRefused` and build no HTTP. This is the one
place a refusal becomes an HTTP response, and the only place a
`Retry-After` header is made; `openai_errors` then puts it in the envelope
an OpenAI SDK reads.
"""

from __future__ import annotations

from types import TracebackType

from fastapi import HTTPException

from app.services.ai.chat.errors import ChatRefused


class ChatRefusals:
    """`with ChatRefusals(): ...` turns a `ChatRefused` into its status, detail and `Retry-After`.

    Anything else passes through untouched.
    """

    def __enter__(self) -> ChatRefusals:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if isinstance(exc, ChatRefused):
            headers = {'Retry-After': str(exc.retry_after)} if exc.retry_after is not None else None
            raise HTTPException(status_code=exc.status_code, detail=exc.detail, headers=headers) from exc
