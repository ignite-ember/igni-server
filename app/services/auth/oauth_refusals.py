"""The answer when a social sign-in refuses.

`OAuthService` raises typed errors and decides no status code. This is
the one place they become HTTP responses.
"""

from __future__ import annotations

from types import TracebackType

from fastapi import HTTPException, status

from app.services.auth.oauth_errors import OAuthRefused


class OAuthRefusals:
    """`with OAuthRefusals(): ...` turns a sign-in refusal into a 400.

    Anything else passes through untouched.
    """

    def __enter__(self) -> OAuthRefusals:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if isinstance(exc, OAuthRefused):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
