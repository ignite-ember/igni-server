"""The answer when a discard or an erasure report is refused.

`Erasures` raises typed errors and decides no status code. This is the one
place they become HTTP responses, with the status and words callers have
always been given.
"""

from __future__ import annotations

from types import TracebackType

from fastapi import HTTPException, status

from app.services.cloud.erasure_errors import CurrentMemberKept, LegalHoldStands, NoSuchIncarnation, NothingToDiscard


class ErasureRefusals:
    """`with ErasureRefusals(): ...` turns an erasure refusal into a 404 or 409.

    Anything else passes through untouched.
    """

    def __enter__(self) -> ErasureRefusals:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if isinstance(exc, LegalHoldStands | CurrentMemberKept):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        if isinstance(exc, NothingToDiscard | NoSuchIncarnation):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
