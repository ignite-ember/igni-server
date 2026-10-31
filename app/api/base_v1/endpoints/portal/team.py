"""Portal team management — list members, change roles (org-scoped)."""

from fastapi import APIRouter, BackgroundTasks, Depends, status

from app.api.base_v1.schema.portal.team import (
    InvitedMemberResponse,
    InviteMemberRequest,
    RoleChangeRequest,
    RoleChangeResponse,
    SessionsWithdrawnResponse,
    StatusChangeRequest,
    StatusChangeResponse,
    TeamMemberList,
)
from app.api.base_v1.schema.portal.user import PortalUser
from app.security.portal_auth import require_admin
from app.services.email.delivery import EmailDelivery, invite_url_for
from app.services.email.service import EmailService
from app.services.portal.team_service import TeamService

router = APIRouter()

_delivery = EmailDelivery()


@router.get(
    '/team',
    response_model=TeamMemberList,
    status_code=status.HTTP_200_OK,
    description='List all team members in the current organization',
)
async def list_team_members(
    current_user: PortalUser = Depends(require_admin),
) -> TeamMemberList:
    """Return all portal users in the active org with usage stats. Admin-only."""
    return await TeamService().list_members(org_id=current_user.org_id)


@router.post(
    '/team/invite',
    response_model=InvitedMemberResponse,
    status_code=status.HTTP_201_CREATED,
    description='Invite a new member to the organization',
)
async def invite_member(
    body: InviteMemberRequest,
    bg: BackgroundTasks,
    current_user: PortalUser = Depends(require_admin),
) -> InvitedMemberResponse:
    """Create a new org member with status 'invited'. Admin-only.

    The response carries the claim link whether or not we email it, so
    an admin can pass it on directly — and so a deployment with no relay
    can still invite people, which was not previously true.
    """
    result = await TeamService().invite_member(
        email=body.email,
        role=body.role,
        current_user=current_user,
    )

    emailed = _delivery.enabled
    if emailed:
        inviter_name = current_user.full_name or current_user.email or 'A team admin'
        bg.add_task(
            EmailService().send_team_invite,
            to_email=body.email,
            role=body.role,
            invited_by=inviter_name,
        )
    else:
        _delivery.note_undeliverable(purpose='team invite', email=body.email)

    return InvitedMemberResponse(
        **result.model_dump(),
        invite_url=invite_url_for(body.email),
        emailed=emailed,
    )


@router.patch(
    '/team/{user_id}/role',
    response_model=RoleChangeResponse,
    status_code=status.HTTP_200_OK,
    description="Change a team member's role in the organization",
)
async def change_member_role(
    user_id: str,
    body: RoleChangeRequest,
    current_user: PortalUser = Depends(require_admin),
) -> RoleChangeResponse:
    """Promote or demote a user. Admin-only. Cannot change own role."""
    return await TeamService().change_role(
        user_id=user_id,
        new_role=body.role,
        current_user=current_user,
    )


@router.patch(
    '/team/{user_id}/status',
    response_model=StatusChangeResponse,
    status_code=status.HTTP_200_OK,
    description='Activate or deactivate a team member',
)
async def change_member_status(
    user_id: str,
    body: StatusChangeRequest,
    current_user: PortalUser = Depends(require_admin),
) -> StatusChangeResponse:
    """Activate or deactivate a user. Admin-only. Cannot deactivate self."""
    return await TeamService().change_status(
        user_id=user_id,
        new_status=body.status,
        current_user=current_user,
    )


@router.post(
    '/team/{user_id}/revoke-sessions',
    response_model=SessionsWithdrawnResponse,
    status_code=status.HTTP_200_OK,
    description='Refuse every token this member already holds',
)
async def revoke_member_sessions(
    user_id: str,
    current_user: PortalUser = Depends(require_admin),
) -> SessionsWithdrawnResponse:
    """Sign a member out of every device. Admin-only, org-scoped.

    `POST` and not `PATCH`: it is not an edit to a member's
    representation, it is an action with an effect elsewhere — and the
    audit middleware records mutations under `/v1/portal` structurally,
    so this is in the trail without a line of code here (F114).
    """
    return await TeamService().withdraw_sessions(user_id=user_id, current_user=current_user)


@router.delete(
    '/team/{user_id}',
    status_code=status.HTTP_204_NO_CONTENT,
    description='Remove a team member from the organization',
)
async def remove_member(
    user_id: str,
    current_user: PortalUser = Depends(require_admin),
) -> None:
    """Permanently remove a user from the org. Admin-only. Cannot remove self."""
    await TeamService().remove_member(user_id=user_id, current_user=current_user)
