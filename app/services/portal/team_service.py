"""Service layer for portal team management (org-scoped)."""

import logging

from fastapi import HTTPException, status

from app.api.base_v1.schema.portal.team import (
    RoleChangeResponse,
    SessionsWithdrawnResponse,
    StatusChangeResponse,
    TeamMemberList,
    TeamMemberResponse,
)
from app.api.base_v1.schema.portal.user import PortalUser
from app.db.sql.models.user import User
from app.db.sql.models.user_role import Role
from app.db.sql.org_domains import OrgDomainDB
from app.db.sql.team import TeamDB
from app.db.sql.tenancy import TenantData
from app.db.sql.user import UserDB
from app.security.account_domains import email_domain
from app.services.cloud.revocations import Revocations
from app.services.cloud.vm_credentials import VmCredentials

logger = logging.getLogger(__name__)


class TeamService:
    """Business logic for team management, scoped to the active org.
    Two DB handles were module-level instances every method reached
    for, and the class was a namespace of `@staticmethod`s over them —
    nothing here could be exercised against a substitute without
    patching the module.
    """

    def __init__(self, *, team: TeamDB | None = None, users: UserDB | None = None) -> None:
        self._db = team or TeamDB()
        self._user_db = users or UserDB()

    async def list_members(self, org_id: str) -> TeamMemberList:
        rows, total = await self._db.list_members(org_id)

        user_ids: list[str] = [r.user_id for r in rows]  # type: ignore[misc]

        # Resolve names/emails from users table
        user_profiles: dict[str, tuple[str | None, str | None]] = {}
        for uid in user_ids:
            user = await self._user_db.get_by_user_id(uid)
            if user:
                user_profiles[uid] = (user.full_name, user.email)  # type: ignore[assignment]

        members = []
        for r in rows:
            full_name, email = user_profiles.get(r.user_id, (None, None))  # type: ignore[call-overload]
            # Fall back to invited_email if no user profile
            display_email = email or r.invited_email
            members.append(
                TeamMemberResponse(
                    id=r.user_id,
                    user_id=r.user_id,
                    email=display_email,
                    full_name=full_name,
                    role=r.role,
                    status=r.status or 'active',
                    total_sessions=0,
                    total_tokens=0,
                    last_active=None,
                    joined_at=r.created_at,
                    assigned_by=r.invited_by,
                )
            )

        return TeamMemberList(members=members, total=total)

    async def invite_member(
        self,
        email: str,
        role: str,
        current_user: PortalUser,
    ) -> TeamMemberResponse:
        valid_roles = {r.value for r in Role}
        if role not in valid_roles:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f'Invalid role. Must be one of: {", ".join(valid_roles)}',
            )

        # A person belongs to the organisation owning their address's
        # domain, so an invitation outside it could never be claimed.
        domains = await OrgDomainDB().domains_of(current_user.org_id)
        if email_domain(email) not in domains:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"{email} is not on one of this organisation's domains ({', '.join(domains) or 'none yet'}). "
                    'A super admin can add a domain.'
                ),
            )

        account = await self._user_db.by_address(email)
        if account is not None:
            return await self._add_existing(account, role=role, current_user=current_user)

        existing = await self._db.get_member_by_email(current_user.org_id, email)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f'A member with email {email} already exists in this organization',
            )

        member = await self._db.create_invited_member(
            org_id=current_user.org_id,
            email=email,
            role=role,
            invited_by=current_user.user_id,
        )

        return TeamMemberResponse(
            id=member.user_id,
            user_id=member.user_id,
            email=email,
            full_name=None,
            role=role,
            status='invited',
            total_sessions=0,
            total_tokens=0,
            last_active=None,
            joined_at=member.created_at,
            assigned_by=current_user.user_id,
        )

    async def _add_existing(self, account: User, *, role: str, current_user: PortalUser) -> TeamMemberResponse:
        """An address that already has an account: it is added at once, as signing up with it would.

        The address's domain is this organisation's, which is what decides
        an account's organisation. A new membership — so a person removed
        and added again is a new incarnation, with a fresh VM
        (`docs/service-identity.md` §11.3).
        """
        user_id = str(account.user_id)
        found = await self._db.get_member(current_user.org_id, user_id)
        if found is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f'{account.email} is already in this organization ({found.status})',
            )
        if account.deactivated_at is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f'The account of {account.email} is deactivated; reactivate it first',
            )
        if await TenantData.orgs_of(user_id, active_only=True):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f'{account.email} is a member of another organization',
            )
        member = await self._db.add_existing(current_user.org_id, user_id, role=role, added_by=current_user.user_id)
        return TeamMemberResponse(
            id=user_id,
            user_id=user_id,
            email=str(account.email),
            full_name=account.full_name,
            role=role,
            status='active',
            total_sessions=0,
            total_tokens=0,
            last_active=None,
            joined_at=member.created_at,
            assigned_by=current_user.user_id,
        )

    async def change_role(
        self,
        user_id: str,
        new_role: str,
        current_user: PortalUser,
    ) -> RoleChangeResponse:
        if user_id == current_user.user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Cannot change your own role',
            )

        valid_roles = {r.value for r in Role}
        if new_role not in valid_roles:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f'Invalid role. Must be one of: {", ".join(valid_roles)}',
            )

        old_role = await self._db.update_role(current_user.org_id, user_id, new_role, assigned_by=current_user.user_id)

        return RoleChangeResponse(user_id=user_id, old_role=old_role, new_role=new_role)

    async def change_status(
        self,
        user_id: str,
        new_status: str,
        current_user: PortalUser,
    ) -> StatusChangeResponse:
        if user_id == current_user.user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Cannot change your own status',
            )

        if new_status not in ('active', 'deactivated'):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail='Status must be "active" or "deactivated"',
            )

        # Only active <-> deactivated transitions are allowed. An `invited`
        # member becomes active through the claim flow on signup, not via
        # this endpoint — otherwise an admin could "reactivate" someone who
        # never accepted the invitation.
        member = await self._db.get_member(current_user.org_id, user_id)
        if not member:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Member not found')
        if member.status not in ('active', 'deactivated'):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'Cannot change status of a member who is {member.status}',
            )

        old_status = await self._db.update_status(current_user.org_id, user_id, new_status)

        # And on the account, which is what actually deprovisions them.
        # The membership row is read by the portal's own dependency and
        # by nothing else, so setting it alone left a deactivated person
        # holding their documents, their notifications and the ability
        # to publish until their token expired. Deactivating withdraws
        # their sessions in the same statement; reactivating does not
        # re-admit the ones they held before they left.
        await self._user_db.set_deactivated(user_id, deactivated=new_status == 'deactivated')
        if new_status == 'deactivated':
            await VmCredentials().revoke_person(user_id)

        return StatusChangeResponse(user_id=user_id, old_status=old_status, new_status=new_status)

    async def withdraw_sessions(self, user_id: str, current_user: PortalUser) -> SessionsWithdrawnResponse:
        """Refuse every token a member of *this* org already holds.

        DEC-10/B5, and the lever AC-11 said did not exist. Deactivation
        (`change_status`) cuts organisation access on the next request but
        leaves the credential working — `test_what_deactivation_actually_
        revokes.py` proves that — so an operator facing a leaked token had
        nothing short of deleting the person. This is that lever.

        Membership is checked before the write, and that check is the
        authorisation boundary rather than a tidiness one: `require_admin`
        establishes that the caller administers *some* organisation, and
        without this an admin of any org could sign out any user of the
        deployment by id. A denial of service against a stranger, granted
        by an endpoint whose purpose is to stop one.

        Self-revocation is allowed here — an admin who suspects their own
        laptop is what leaked should not have to find the other button —
        unlike `change_role` and `change_status`, where the guard exists
        to stop an admin locking the last administrator out of the org.
        Signing yourself out is reversible by signing in again.
        """
        member = await self._db.get_member(current_user.org_id, user_id)
        if not member:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Member not found')

        watermark = await self._user_db.withdraw_sessions(user_id)
        await VmCredentials().revoke_person(user_id)
        await Revocations().sessions_withdrawn(user_id, watermark)
        logger.info(
            'Sessions withdrawn for %s in org %s by %s, valid after %s',
            user_id,
            current_user.org_id,
            current_user.user_id,
            watermark.isoformat(),
        )
        return SessionsWithdrawnResponse(user_id=user_id, tokens_valid_after=watermark)

    async def remove_member(self, user_id: str, current_user: PortalUser) -> None:
        if user_id == current_user.user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Cannot remove yourself',
            )

        await self._db.delete(current_user.org_id, user_id)
        await VmCredentials().revoke_person(user_id, org_ids=[current_user.org_id])
