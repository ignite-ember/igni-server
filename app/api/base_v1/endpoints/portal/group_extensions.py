"""Portal group extensions: which approved extensions a group's people get (off by default)."""

from fastapi import APIRouter, Depends, status

from app.api.base_v1.schema.portal.groups import GroupExtensionList, SetGroupExtension
from app.api.base_v1.schema.portal.user import PortalUser
from app.security.portal_auth import require_admin
from app.services.portal.group_extensions import GroupExtensions

router = APIRouter()


@router.get(
    '/groups/{group_id}/extensions',
    response_model=GroupExtensionList,
    status_code=status.HTTP_200_OK,
)
async def list_group_extensions(
    group_id: str,
    current_user: PortalUser = Depends(require_admin),
) -> GroupExtensionList:
    """Every approved extension, each with whether this group has it on. Admin-only."""
    return await GroupExtensions().of_group(current_user.org_id, group_id)


@router.put(
    '/groups/{group_id}/extensions/{extension_id}',
    status_code=status.HTTP_204_NO_CONTENT,
)
async def set_group_extension(
    group_id: str,
    extension_id: str,
    body: SetGroupExtension,
    current_user: PortalUser = Depends(require_admin),
) -> None:
    """Switch an approved extension on or off for this group's people. Admin-only."""
    await GroupExtensions().set(
        current_user.org_id,
        group_id=group_id,
        extension_id=extension_id,
        enabled=body.enabled,
        by_user_id=current_user.user_id,
    )
