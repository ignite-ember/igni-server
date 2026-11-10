"""The answer when a VM profile request is refused.

`VmProfileRequests` raises typed errors and decides no status code. This
is the one place they become HTTP responses, with the status and words
callers have always been given.
"""

from __future__ import annotations

from types import TracebackType

from fastapi import HTTPException, status

from app.services.cloud.vm_profile_request_errors import (
    AlreadyApproved,
    AlreadyDecided,
    BeyondCaps,
    NoAppRequest,
    NoSuchRequest,
    NotTheApp,
    ProfileNamesTaken,
    RequestChanged,
)


class VmProfileRequestRefusals:
    """`with VmProfileRequestRefusals(): ...` turns a request refusal into a 400, 403, 404 or 409.

    Anything else passes through untouched.
    """

    def __enter__(self) -> VmProfileRequestRefusals:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if isinstance(exc, BeyondCaps):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if isinstance(exc, NotTheApp):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if isinstance(exc, NoAppRequest | NoSuchRequest):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        if isinstance(exc, RequestChanged | AlreadyApproved | AlreadyDecided | ProfileNamesTaken):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
