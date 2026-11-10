"""The answer when an item, or a request about items, is refused.

`ProducerEvents` and `EventRateLimit` raise typed errors and decide no
status code. This is the one place they become HTTP responses, with the
status and words callers have always been given.
"""

from __future__ import annotations

from types import TracebackType

from fastapi import HTTPException, status

from app.db.sql.producer_events import AttachmentsUnavailable
from app.services.cloud.producer_event_errors import (
    ExpiryPast,
    ExtensionOff,
    NoSuchEvent,
    NotAPerson,
    PayloadTooLarge,
    TooManyEvents,
    ViewTooLarge,
)


class ProducerEventRefusals:
    """`with ProducerEventRefusals(): ...` turns an item refusal into a 400, 403, 404, 413 or 429.

    Anything else passes through untouched.
    """

    def __enter__(self) -> ProducerEventRefusals:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if isinstance(exc, TooManyEvents):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=str(exc),
                headers={'Retry-After': str(exc.retry_after)},
            ) from exc
        if isinstance(exc, ExpiryPast | AttachmentsUnavailable):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if isinstance(exc, PayloadTooLarge | ViewTooLarge):
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)) from exc
        if isinstance(exc, ExtensionOff | NotAPerson):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if isinstance(exc, NoSuchEvent):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
