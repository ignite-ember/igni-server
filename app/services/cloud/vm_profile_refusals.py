"""The answer when a VM profile operation is refused.

`VmProfiles` raises typed errors and decides no status code. This is the
one place they become HTTP responses, with the status and words callers
have always been given.
"""

from __future__ import annotations

from types import TracebackType

from fastapi import HTTPException, status

from app.services.cloud.vm_profile_errors import (
    AppNotFound,
    DefaultProfileKept,
    GroupNotFound,
    ProducerNotInOrg,
    ProfileBeyondCaps,
    ProfileNameTaken,
    ProfileNotFound,
)


class VmProfileRefusals:
    """`with VmProfileRefusals(): ...` turns a profile refusal into a 400, 404 or 409.

    Anything else passes through untouched.
    """

    def __enter__(self) -> VmProfileRefusals:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if isinstance(exc, ProfileBeyondCaps):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if isinstance(exc, ProfileNotFound | GroupNotFound | AppNotFound | ProducerNotInOrg):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        if isinstance(exc, ProfileNameTaken | DefaultProfileKept):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
