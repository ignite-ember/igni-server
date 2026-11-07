"""Portal groups: list, create, read, update, delete and copy."""

from fastapi import APIRouter, Depends, status

from app.api.base_v1.schema.portal.groups import (
    CopyGroupRequest,
    CreateGroupRequest,
    OrgGroupDetail,
    OrgGroupList,
    OrgGroupResponse,
    UpdateGroupRequest,
)
from app.api.base_v1.schema.portal.user import PortalUser
from app.security.portal_auth import get_current_portal_user, require_admin
from app.services.portal.group_refusals import GroupRefusals
from app.services.portal.groups_service import GroupsService

router = APIRouter()


@router.get('/groups', response_model=OrgGroupList, status_code=status.HTTP_200_OK)
async def list_groups(
    offset: int = 0,
    limit: int | None = None,
    current_user: PortalUser = Depends(get_current_portal_user),
) -> OrgGroupList:
    """List all groups in the org. Any authenticated org member.

    Readable by members, not just admins: ``test_list_groups_as_member``
    asserts a 200 for a plain member while
    ``test_create_group_non_admin_forbidden`` asserts a 403 for the same user
    creating one — reads open, writes admin-only. The result is scoped to
    ``current_user.org_id``, so a member only ever sees their own org.
    """
    with GroupRefusals():
        return await GroupsService().list_groups(
            org_id=current_user.org_id,
            offset=offset,
            limit=limit,
            by_user_id=current_user.user_id,
        )


@router.post('/groups', response_model=OrgGroupResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    body: CreateGroupRequest,
    current_user: PortalUser = Depends(require_admin),
) -> OrgGroupResponse:
    """Create a new group. Admin-only."""
    with GroupRefusals():
        return await GroupsService().create_group(
            org_id=current_user.org_id, body=body, by_user_id=current_user.user_id
        )


@router.get('/groups/{group_id}', response_model=OrgGroupDetail, status_code=status.HTTP_200_OK)
async def get_group(
    group_id: str,
    current_user: PortalUser = Depends(require_admin),
) -> OrgGroupDetail:
    """Get a single group with member and entry counts. Admin-only."""
    with GroupRefusals():
        return await GroupsService().get_group(
            org_id=current_user.org_id, group_id=group_id, by_user_id=current_user.user_id
        )


@router.patch('/groups/{group_id}', response_model=OrgGroupResponse, status_code=status.HTTP_200_OK)
async def update_group(
    group_id: str,
    body: UpdateGroupRequest,
    current_user: PortalUser = Depends(require_admin),
) -> OrgGroupResponse:
    """Update a group's name or description. Admin-only."""
    with GroupRefusals():
        return await GroupsService().update_group(org_id=current_user.org_id, group_id=group_id, body=body)


@router.delete('/groups/{group_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: str,
    current_user: PortalUser = Depends(require_admin),
) -> None:
    """Delete a group and its entries. Its people go back to the Default group. Admin-only."""
    with GroupRefusals():
        await GroupsService().delete_group(
            org_id=current_user.org_id, group_id=group_id, by_user_id=current_user.user_id
        )


@router.post('/groups/{group_id}/copy', response_model=OrgGroupResponse, status_code=status.HTTP_201_CREATED)
async def copy_group(
    group_id: str,
    body: CopyGroupRequest,
    current_user: PortalUser = Depends(require_admin),
) -> OrgGroupResponse:
    """Copy a group's entries, extensions and settings into a new group with nobody in it. Admin-only."""
    with GroupRefusals():
        return await GroupsService().copy_group(
            org_id=current_user.org_id, group_id=group_id, body=body, by_user_id=current_user.user_id
        )
