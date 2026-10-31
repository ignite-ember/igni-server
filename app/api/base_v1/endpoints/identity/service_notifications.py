"""``POST /v1/service-notifications``: a service telling people something.

``docs/service-identity.md`` §10.1. With a service token, for the token's
organisation only. The service says what happened and who it concerns;
:class:`~app.services.notifications.delivery.NotificationDelivery`
decides who that really is, where it came from, and whether they have
already been told. The service SDK offers this as ``host.notify``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.base_v1.schema.notification import (
    Delivered,
    MAX_BATCH,
    MAX_RECIPIENTS,
    ServiceNotifications,
)
from app.security.service_caller import require_service, ServiceCaller
from app.services.notifications import NoExtension, NotAPage, NotificationDelivery

router = APIRouter()


@router.post('/service-notifications', response_model=Delivered, tags=['Directory'])
async def notify(
    body: ServiceNotifications,
    caller: ServiceCaller = Depends(require_service),
    delivery: NotificationDelivery = Depends(NotificationDelivery.default),
) -> Delivered:
    if len(body.notifications) > MAX_BATCH:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f'At most {MAX_BATCH} notifications per call; got {len(body.notifications)}.',
        )
    for index, notification in enumerate(body.notifications):
        if len(notification.recipients) > MAX_RECIPIENTS:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=(
                    f'At most {MAX_RECIPIENTS} recipients per notification; '
                    f'notification {index} has {len(notification.recipients)}.'
                ),
            )
    try:
        delivered = await delivery.deliver(caller, body.notifications)
    except NoExtension as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except NotAPage as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return Delivered(delivered=delivered)
