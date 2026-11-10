"""The portal's answer when the team store refuses a membership change.

The store raises typed errors and decides no status code. This is the one
place they become HTTP responses, with the status and words the portal
has always given.
"""

from __future__ import annotations

from types import TracebackType

from fastapi import HTTPException, status

from app.db.sql.schemas.membership_errors import MembershipConflict, MembershipNotFound


class TeamRefusals:
    """`with TeamRefusals(): ...` turns a team-store refusal into the portal's 404 or 409.

    Anything else passes through untouched.
    """

    def __enter__(self) -> TeamRefusals:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if isinstance(exc, MembershipConflict):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        if isinstance(exc, MembershipNotFound):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
