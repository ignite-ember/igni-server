"""Portal user endpoint — current user profile with portal role."""

from fastapi import APIRouter, BackgroundTasks, Depends, status

from app.api.base_v1.schema.auth import AuthResponse
from app.api.base_v1.schema.portal.team import SessionsWithdrawnResponse
from app.api.base_v1.schema.portal.user import (
    CompanyAddressConfirm,
    CompanyAddressRequest,
    DeleteAccountResponse,
    PortalAccount,
    UpdateProfileRequest,
    UserMeResponse,
)
from app.security.portal_auth import get_current_account
from app.services.portal.company_address import CompanyAddress
from app.services.portal.session_withdrawal import SessionWithdrawal
from app.services.portal.user_service import UserService

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
    watermark = await SessionWithdrawal().withdraw(current_user.user_id)
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


@router.post(
    '/me/company-address/code',
    response_model=AuthResponse,
    status_code=status.HTTP_200_OK,
    description='Send a code proving a company address, for an account in no organisation.',
)
async def send_company_address_code(
    body: CompanyAddressRequest,
    bg: BackgroundTasks,
    current_user: PortalAccount = Depends(get_current_account),
) -> AuthResponse:
    """The bootstrap account's first step onto its company's domain."""
    message = await CompanyAddress().send_code(bg, current_user, str(body.email))
    return AuthResponse(message=message)


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
    return await CompanyAddress().adopt(current_user, str(body.email), body.code)
