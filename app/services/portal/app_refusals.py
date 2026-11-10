"""The portal's answer when the app services refuse a request.

The services raise typed errors and decide no status code. This is the
one place they become HTTP responses, with the status and the words the
portal has always given.
"""

from __future__ import annotations

from types import TracebackType

from fastapi import HTTPException, status

from app.services.identity.jwk import KeysRefused, TooManyKeys
from app.services.portal.app_errors import AppKeyTaken, AppNotFound, ExtensionNotShipped, OwnerNotInOrg


class AppRefusals:
    """`with AppRefusals(): ...` turns an app refusal into the portal's 400, 404 or 409.

    Anything unanticipated passes through untouched, to surface as the 500 it is.
    """

    def __enter__(self) -> AppRefusals:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if isinstance(exc, TooManyKeys):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f'At most {exc.limit} keys per app'
            ) from exc
        if isinstance(exc, KeysRefused | OwnerNotInOrg):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if isinstance(exc, AppNotFound | ExtensionNotShipped):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        if isinstance(exc, AppKeyTaken):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
