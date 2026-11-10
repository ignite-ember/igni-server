"""`/v1/apps/{app_id}/spaces`: an app declares a space — a producer of its own — per end user.

The app's own token only, and only for its own spaces
(`docs/service-identity.md` §17.1).
"""

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.api.base_v1.schema.spaces import EXTERNAL_ID_PATTERN, SpacePage, SpaceRecord
from app.security.producer import ProducerCaller, require_producer
from app.services.cloud.spaces import Spaces
from app.services.cloud.vm_credential_records import VmCredentialRecord

router = APIRouter(prefix='/apps/{app_id}/spaces')

ExternalId = Path(pattern=EXTERNAL_ID_PATTERN, description="The app's own name for its user")


@router.put('/{external_id}', response_model=SpaceRecord, status_code=status.HTTP_200_OK)
async def declare(
    app_id: str, response: Response, external_id: str = ExternalId, caller: ProducerCaller = Depends(require_producer)
) -> SpaceRecord:
    """201 with the new space; 200 with the one already there."""
    space, created = await Spaces().declare(caller, app_id, external_id)
    if created:
        response.status_code = status.HTTP_201_CREATED
    return space


@router.get('/{external_id}', response_model=SpaceRecord)
async def get(
    app_id: str, external_id: str = ExternalId, caller: ProducerCaller = Depends(require_producer)
) -> SpaceRecord:
    return await Spaces().get(caller, app_id, external_id)


@router.get('', response_model=SpacePage)
async def page(
    app_id: str,
    after: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=1000),
    caller: ProducerCaller = Depends(require_producer),
) -> SpacePage:
    return await Spaces().page(caller, app_id, after=after, limit=limit)


@router.delete('/{external_id}', status_code=status.HTTP_204_NO_CONTENT)
async def remove(
    app_id: str, external_id: str = ExternalId, caller: ProducerCaller = Depends(require_producer)
) -> Response:
    """The space is gone; the feed tells the gateway to erase its VM."""
    await Spaces().remove(caller, app_id, external_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post('/{external_id}/vm-credential', response_model=VmCredentialRecord, status_code=status.HTTP_201_CREATED)
async def reissue(
    app_id: str, external_id: str = ExternalId, caller: ProducerCaller = Depends(require_producer)
) -> VmCredentialRecord:
    return await Spaces().reissue(caller, app_id, external_id)
