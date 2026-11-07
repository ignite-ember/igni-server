"""Portal group members: who is in which group, and moving people between groups."""

from fastapi import APIRouter, Depends, status

from app.api.base_v1.schema.portal.groups import (
    AddMemberRequest,
    OrgGroupMemberList,
    OrgGroupMembershipList,
    OrgGroupSummary,
)
from app.api.base_v1.schema.portal.user import PortalUser
from app.security.portal_auth import require_admin
from app.services.portal.group_members_service import GroupMembersService
from app.services.portal.group_refusals import GroupRefusals

router = APIRouter()


@router.get('/group-memberships', response_model=OrgGroupMembershipList, status_code=status.HTTP_200_OK)
async def list_group_memberships(
    current_user: PortalUser = Depends(require_admin),
) -> OrgGroupMembershipList:
    """Every active person of the organisation and the one group they are in. Admin-only."""
    with GroupRefusals():
        return await GroupMembersService().memberships(org_id=current_user.org_id, by_user_id=current_user.user_id)


@router.get('/groups/{group_id}/members', response_model=OrgGroupMemberList, status_code=status.HTTP_200_OK)
async def list_group_members(
    group_id: str,
    current_user: PortalUser = Depends(require_admin),
) -> OrgGroupMemberList:
    """List all members of a group. Admin-only, and admin *of this org*.

    ``require_admin`` proves the caller is an administrator of some
    organization, not of the one that owns this group — passing
    ``org_id`` is what turns the first into the second.
    """
    with GroupRefusals():
        return await GroupMembersService().list_members(
            org_id=current_user.org_id, group_id=group_id, by_user_id=current_user.user_id
        )


@router.post(
    '/groups/{group_id}/members',
    response_model=OrgGroupMemberList,
    status_code=status.HTTP_200_OK,
)
async def add_group_member(
    group_id: str,
    body: AddMemberRequest,
    current_user: PortalUser = Depends(require_admin),
) -> OrgGroupMemberList:
    """Put an org member in this group, moving them out of their current one. Admin-only.

    A person is in exactly one group, so this is the only way into one:
    there is no separate "move".
    """
    members = GroupMembersService()
    with GroupRefusals():
        await members.add_member(
            org_id=current_user.org_id, group_id=group_id, user_id=body.user_id, by_user_id=current_user.user_id
        )
        # Return updated member list, scoped like the mutation above it.
        return await members.list_members(org_id=current_user.org_id, group_id=group_id)


@router.delete(
    '/groups/{group_id}/members/{user_id}',
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_group_member(
    group_id: str,
    user_id: str,
    current_user: PortalUser = Depends(require_admin),
) -> None:
    """Take a person out of this group, back into the Default one. Admin-only.

    409 on the Default group itself: nobody leaves it except by being
    put in another group.
    """
    with GroupRefusals():
        await GroupMembersService().remove_member(
            org_id=current_user.org_id, group_id=group_id, user_id=user_id, by_user_id=current_user.user_id
        )


@router.get(
    '/users/{user_id}/group',
    response_model=OrgGroupSummary | None,
    status_code=status.HTTP_200_OK,
)
async def get_user_group(
    user_id: str,
    current_user: PortalUser = Depends(require_admin),
) -> OrgGroupSummary | None:
    """Get the group a user belongs to. Admin-only, scoped to this org."""
    with GroupRefusals():
        return await GroupMembersService().get_user_group(org_id=current_user.org_id, user_id=user_id)
