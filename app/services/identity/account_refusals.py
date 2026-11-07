"""The answer when the account store refuses a change.

The store raises typed errors and decides no status code. This is the one
place they become HTTP responses, with the status and words callers have
always been given.
"""

from __future__ import annotations

from types import TracebackType

from fastapi import HTTPException, status

from app.db.sql.schemas.account_errors import AccountConflict, AccountNotFound, PersonalAddress


class AccountRefusals:
    """`with AccountRefusals(): ...` turns an account-store refusal into a 404, 409 or 422.

    422 for a personal address rather than 400: the address is the wrong
    *kind* of address, which is a fact about the field rather than about
    the request, and it is the code every other "this value will not do"
    answer on the sign-up form already uses.

    Anything else passes through untouched.
    """

    def __enter__(self) -> AccountRefusals:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if isinstance(exc, AccountConflict):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        if isinstance(exc, AccountNotFound):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        if isinstance(exc, PersonalAddress):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
