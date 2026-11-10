"""Super admins: who they are, and making or unmaking one.

A super admin owns the install: creates organisations, makes other
super admins, and changes deployment-wide settings. The first account
a deployment creates is one (`UserDB.create`); after that only a super
admin can make another, and only of somebody on a domain an
organisation here owns. The last one cannot be removed, by anybody,
including themselves — there would be no way back from inside the
product.

Named by address when making one, because that is what a person types;
by account id when unmaking one, because that is what the list shows.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr

from app.api.base_v1.schema.portal.user import PortalAccount
from app.db.sql.models.user import User
from app.db.sql.org_domains import OrgDomainDB
from app.db.sql.schemas.account_errors import LastSuperAdmin
from app.db.sql.super_admins import SuperAdminDB
from app.db.sql.user import UserDB
from app.security.super_admin import require_super_admin

router = APIRouter()


class SuperAdmin(BaseModel):
    user_id: str
    email: str | None = None
    full_name: str | None = None

    @classmethod
    def of(cls, user: User) -> SuperAdmin:
        return cls(user_id=str(user.user_id), email=user.email, full_name=user.full_name)


class MakeSuperAdmin(BaseModel):
    email: EmailStr


@router.get('/super-admins', response_model=list[SuperAdmin])
async def list_super_admins(_account: PortalAccount = Depends(require_super_admin)) -> list[SuperAdmin]:
    """Everyone who owns this install, oldest account first."""
    return [SuperAdmin.of(user) for user in await SuperAdminDB().super_admins()]


@router.post('/super-admins', response_model=SuperAdmin, status_code=status.HTTP_200_OK)
async def make_super_admin(
    body: MakeSuperAdmin,
    _account: PortalAccount = Depends(require_super_admin),
) -> SuperAdmin:
    """Make an existing account a super admin. Idempotent.

    The account must exist: a person signs up first. There is no
    invitation to super admin, because there is nothing to accept — it
    is a role on an account, not membership of anything.
    """
    found = await UserDB().get_by_email(str(body.email).strip())
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='No account with that address. They need to sign up first.',
        )
    # A super admin is a real person in the company: somebody on a
    # domain an organisation here owns. The bootstrap account is the
    # one exception, and it was made one by being first, not here.
    if await OrgDomainDB().org_for_address(found.email) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="That account isn't on any organisation's domain, so it can't be a super admin.",
        )
    made = await SuperAdminDB().set_super_admin(str(found.user_id), super_admin=True)
    if made is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No such account.')
    return SuperAdmin.of(made)


@router.delete('/super-admins/{user_id}', status_code=status.HTTP_204_NO_CONTENT)
async def unmake_super_admin(
    user_id: str,
    _account: PortalAccount = Depends(require_super_admin),
) -> Response:
    """Stop an account being a super admin. 409 for the last one."""
    try:
        unmade = await SuperAdminDB().set_super_admin(user_id, super_admin=False)
    except LastSuperAdmin as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='This is the only super admin. Make somebody else one first.',
        ) from exc
    if unmade is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No such account.')
    return Response(status_code=status.HTTP_204_NO_CONTENT)
