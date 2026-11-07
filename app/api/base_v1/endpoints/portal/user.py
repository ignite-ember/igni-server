"""Portal user endpoint — current user profile with portal role."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr

from app.api.base_v1.schema.auth import AuthResponse
from app.api.base_v1.schema.portal.team import SessionsWithdrawnResponse
from app.api.base_v1.schema.portal.user import (
    PortalAccount,
    UpdateProfileRequest,
    UserMeResponse,
)
from app.db.sql.org_domains import NOT_A_COMPANY_ADDRESS, OrgDomainDB
from app.db.sql.user import UserDB
from app.security.portal_auth import get_current_account
from app.services.auth.otp_service import CodeSendThrottled, OTPService
from app.services.cloud.revocations import Revocations
from app.services.cloud.vm_credentials import VmCredentials
from app.services.email.delivery import EmailDelivery
from app.services.email.service import EmailService
from app.services.identity.api_keys import ApiKeys
from app.services.portal.user_service import UserService

_user_db = UserDB()


class DeleteAccountResponse(BaseModel):
    message: str


router = APIRouter()


@router.get(
    '/me',
    response_model=UserMeResponse,
    status_code=status.HTTP_200_OK,
    description='Get current user profile with portal role',
)
async def get_me(
    current_user: PortalAccount = Depends(get_current_account),
) -> UserMeResponse:
    """Return the current user's profile with portal role."""
    return await UserService().get_me(current_user)


@router.patch(
    '/me',
    response_model=UserMeResponse,
    status_code=status.HTTP_200_OK,
    description='Update current user profile',
)
async def update_me(
    body: UpdateProfileRequest,
    current_user: PortalAccount = Depends(get_current_account),
) -> UserMeResponse:
    """Update the current user's profile fields."""
    return await UserService().update_me(body, current_user)


# ``POST /me/set-password`` was here, and it was an account-takeover
# primitive. Removed rather than repaired — F110.
#
# It replaced the account password given nothing but a session token: no
# current password, and no check whether one already existed. So anybody
# holding a token could set a password of their own and lock the owner
# out, turning a leaked session into a permanent credential that survives
# the token expiring. Demonstrated end to end before removal: original
# password 200, set-password with only a token 200, attacker's password
# 200, original password 401.
#
# It also bypassed the password policy the rest of the application
# enforces. `SetPasswordRequest` never applied
# `_validate_password_strength`, so a one-character password was accepted
# here and then worked at sign-in, while sign-up and reset both demand
# eight characters with mixed classes.
#
# Removed rather than fixed for three reasons, in order of weight:
#
#   * nothing called it. No portal page, no CLI path;
#   * its stated purpose — letting an OAuth user add a password — is
#     already served by `POST /send-reset-password-code` followed by
#     `POST /reset-password`, which works for an account with no password
#     and requires proving control of the mailbox. That is a *stronger*
#     check than holding a session token, so a second weaker path was
#     strictly worse;
#   * a change-password endpoint that does the right thing needs the
#     current password, a session-invalidation story, and a notification
#     to the address on file. None of that exists yet, and shipping a
#     third of it would read as though it did.
#
# Same disposition as F10, which removed a routed endpoint that reported
# success without doing anything.


@router.post(
    '/me/revoke-sessions',
    response_model=SessionsWithdrawnResponse,
    status_code=status.HTTP_200_OK,
    description='Sign out of every device, including this one',
)
async def revoke_my_sessions(
    current_user: PortalAccount = Depends(get_current_account),
) -> SessionsWithdrawnResponse:
    """Refuse every token this person holds. DEC-10/B5.

    No admin, no support ticket, and no organisation: this is the route
    somebody takes when they have lost a laptop, and the last thing they
    should need is somebody else's cooperation. It is also the only
    revocation available to a member whose org has no reachable admin.

    **It signs out the caller too**, deliberately. The token in the
    `Authorization` header of *this* request has an `iat` at or before
    the watermark, so the very next request with it is a 401 — which is
    what "sign out everywhere" means, and the alternative (exempting the
    current session) would leave the credential the caller is holding
    working after they asked for it to stop. The portal clears its stored
    token and returns to sign-in on a 200.

    There is nothing to undo. Signing in again mints a token with a
    fresh `iat` after the watermark, which is why one column is enough.
    """
    watermark = await _user_db.withdraw_sessions(current_user.user_id)
    # Their cloud VMs sign out too (docs/service-identity.md §16), and the
    # gateway and services learn of it (§21).
    await VmCredentials().revoke_person(current_user.user_id)
    await ApiKeys().revoke_person(current_user.user_id)
    await Revocations().sessions_withdrawn(current_user.user_id, watermark)
    return SessionsWithdrawnResponse(user_id=current_user.user_id, tokens_valid_after=watermark)


@router.delete(
    '/me',
    response_model=DeleteAccountResponse,
    status_code=status.HTTP_200_OK,
    description='Delete current user account',
)
async def delete_account(
    current_user: PortalAccount = Depends(get_current_account),
) -> DeleteAccountResponse:
    """Permanently delete the current user's account."""
    await UserService().delete_account(current_user)
    return DeleteAccountResponse(message='Account deleted')


class CompanyAddress(BaseModel):
    email: EmailStr


class CompanyAddressConfirm(BaseModel):
    email: EmailStr
    code: str


@router.post(
    '/me/company-address/code',
    response_model=AuthResponse,
    status_code=status.HTTP_200_OK,
    description='Send a code proving a company address, for an account in no organisation.',
)
async def send_company_address_code(
    body: CompanyAddress,
    bg: BackgroundTasks,
    current_user: PortalAccount = Depends(get_current_account),
) -> AuthResponse:
    """The bootstrap account's first step onto its company's domain.

    Only for an account in no organisation: everybody else's address
    already decides which organisation they are in. Checked here as well
    as at confirmation, so nobody is sent a code that could never work.
    """
    if current_user.org_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Your address decides your organisation, so it is not changed here.',
        )
    address = str(body.email).strip()
    if await OrgDomainDB().org_for_address(address) is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NOT_A_COMPANY_ADDRESS)
    try:
        result = await OTPService().generate_address_code(address)
    except CodeSendThrottled:
        return AuthResponse(message=f'A code was sent to {address} recently. Use that one.')
    delivery = EmailDelivery()
    if not delivery.enabled:
        delivery.record_undeliverable_code(purpose='company-address', email=address, code=result.code)
        if delivery.logs_codes:
            return AuthResponse(message=f'No mail relay here: ask the operator for the code sent to {address}.')
        return AuthResponse(message='No mail relay here, so no code can be sent. Ask the operator to configure one.')
    bg.add_task(EmailService().send_sign_up_code, to_email=address, code=result.code)
    return AuthResponse(message=result.message)


@router.post(
    '/me/company-address',
    response_model=UserMeResponse,
    status_code=status.HTTP_200_OK,
    description='Move an account in no organisation onto a proven company address.',
)
async def adopt_company_address(
    body: CompanyAddressConfirm,
    current_user: PortalAccount = Depends(get_current_account),
) -> UserMeResponse:
    """The old address is gone afterwards, and the account is in the
    organisation that owns the new one — as its admin if it has none yet."""
    address = str(body.email).strip()
    await OTPService().validate_address_code(address, body.code)
    joined = await UserDB().move_to_company_address(current_user.user_id, address)
    moved = current_user.model_copy(update={'email': address, 'org_id': str(joined.id), 'org_role': None})
    return await UserService().get_me(moved)
