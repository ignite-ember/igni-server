"""The portal's answer when the group store or the group services refuse a change.

Both raise typed domain errors and decide no status code. This is the one
place they become HTTP responses, with the same status and the same words
the portal has always given.
"""

from __future__ import annotations

from types import TracebackType

from fastapi import HTTPException, status

from app.db.sql.schemas.group import (
    DefaultGroupKeepsItsPeople,
    EntryNotFound,
    GroupConflict,
    GroupNotFound,
    MemberNotFound,
)
from app.services.portal.group_errors import EntryRefused, NotAnOrgMember

#: Names a route, so it is HTTP copy and lives here rather than in the store.
_DEFAULT_KEEPS_ITS_PEOPLE = (
    'Everybody is in exactly one group, and the Default group is where '
    'people go when they leave any other — so nobody leaves it except by '
    'being moved into another group: POST /portal/groups/{group_id}/members.'
)


class GroupRefusals:
    """`with GroupRefusals(): ...` turns a group refusal into the portal's 404, 409 or 422.

    Only the named domain errors are translated, and their messages were
    written for the caller. The services' conflicts (`GroupIsStandard`,
    `ModelNotServed`) are `GroupConflict`s and answer as one. `NoDefaultGroup`
    and anything unanticipated pass through untouched, to surface as the 500
    they are.
    """

    def __enter__(self) -> GroupRefusals:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if isinstance(exc, DefaultGroupKeepsItsPeople):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_DEFAULT_KEEPS_ITS_PEOPLE) from exc
        if isinstance(exc, GroupConflict):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        if isinstance(exc, GroupNotFound | MemberNotFound | EntryNotFound | NotAnOrgMember):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        if isinstance(exc, EntryRefused):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
