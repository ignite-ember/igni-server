"""The answer when authentication refuses.

`AuthService` and `OrgContexts` raise typed errors and decide no status
code. This is the one place they become HTTP responses, with the status
and words callers have always been given.
"""

from __future__ import annotations

from types import TracebackType

from fastapi import HTTPException, status

from app.db.sql.schemas.org_domain import AdmissionRefused
from app.services.auth.auth_errors import InvalidCredentials, InvalidSession, NotAMember


class AuthRefusals:
    """`with AuthRefusals(): ...` turns an authentication refusal into a 401 or 403.

    Anything else passes through untouched.
    """

    def __enter__(self) -> AuthRefusals:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if isinstance(exc, InvalidCredentials | InvalidSession):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        if isinstance(exc, NotAMember | AdmissionRefused):
            # An address this deployment does not take, said in words the person can act on.
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
