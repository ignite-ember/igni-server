"""An admin of one named organisation — or a super admin, who may act in any.

Most portal routes act in the organisation the portal is showing
(`require_admin`). The cloud's VM profiles name the organisation in the
path instead, because a super admin defines and assigns profiles in any
organisation without being a member of it (`docs/service-identity.md` §12).
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel

from app.api.base_v1.schema.portal.user import PortalAccount
from app.db.sql.organizations import OrganizationDB
from app.db.sql.team import TeamDB
from app.security.portal_auth import get_current_account


class OrgAuthority(BaseModel):
    """Who is acting in `org_id`, and as what."""

    org_id: str
    user_id: str
    is_super_admin: bool
    #: Their role in `org_id`; null for a super admin who is not a member.
    org_role: str | None = None

    @property
    def is_org_admin(self) -> bool:
        return self.org_role == 'admin'


class OrgAuthorities:
    async def of(self, account: PortalAccount, org_id: str) -> OrgAuthority:
        """The account's authority in *org_id*; 404 for no such organisation, 403 for no authority."""
        if await OrganizationDB().get_by_id(org_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Organisation not found')
        membership = await TeamDB().get_active_member(org_id, account.user_id)
        role = str(membership.role) if membership is not None else None
        if role != 'admin' and not account.is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail='Only an admin of this organisation, or a super admin'
            )
        return OrgAuthority(
            org_id=org_id, user_id=account.user_id, is_super_admin=account.is_super_admin, org_role=role
        )


async def require_org_authority(org_id: str, account: PortalAccount = Depends(get_current_account)) -> OrgAuthority:
    """An admin of the organisation in the path, or a super admin."""
    return await OrgAuthorities().of(account, org_id)
