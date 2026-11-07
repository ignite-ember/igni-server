"""Sign-in, sign-up, verification codes, password reset, and the CLI's org-scoped token."""

from fastapi import APIRouter, BackgroundTasks, Body, Depends, status
from fastapi.security import OAuth2PasswordBearer

from app.api.base_v1.schema.auth import (
    AuthResponse,
    CLITokenRequest,
    CLITokenResponse,
    ResetNewPasswordRequest,
    SendResetPasswordCodeRequest,
    SendSignInCodeRequest,
    SendSignUpCodeRequest,
    SignInCodeRequest,
    SignInRequest,
    SignUpRequest,
)
from app.services.auth.auth_emails import auth_emails, AuthEmails
from app.services.auth.auth_service import AuthService
from app.services.auth.cli_tokens import cli_tokens, CLITokens
from app.services.auth.jwt_service import TokenResponse
from app.services.auth.otp_service import otp_service, OTPService

_oauth2 = OAuth2PasswordBearer(tokenUrl='token')

router = APIRouter()


@router.post(
    '/sign-in',
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    description='Sign in with email and password',
)
async def sign_in(
    credentials: SignInRequest = Body(..., description='User credentials for sign in'),
) -> TokenResponse:
    return await AuthService.sign_in(credentials)


@router.post(
    '/sign-up',
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    description='Sign up a new user',
)
async def sign_up(
    bg: BackgroundTasks,
    user_data: SignUpRequest = Body(..., description='New user registration details'),
    emails: AuthEmails = Depends(auth_emails),
) -> TokenResponse:
    token = await AuthService.sign_up(user_data)
    emails.welcome(bg, email=str(user_data.email), name=user_data.first_name)
    return token


@router.post(
    '/send-sign-up-code',
    response_model=AuthResponse,
    status_code=status.HTTP_200_OK,
    description='Send sign-up code to email',
)
async def send_sign_up_code(
    bg: BackgroundTasks,
    request: SendSignUpCodeRequest = Body(..., description='Email to send sign-up code to'),
    emails: AuthEmails = Depends(auth_emails),
) -> AuthResponse:
    return AuthResponse(message=await emails.send_sign_up_code(bg, str(request.email)))


@router.post(
    '/send-sign-in-code',
    response_model=AuthResponse,
    status_code=status.HTTP_200_OK,
    description='Send sign-in code to email',
)
async def send_sign_in_code(
    bg: BackgroundTasks,
    request: SendSignInCodeRequest = Body(..., description='Email to send sign-in code to'),
    emails: AuthEmails = Depends(auth_emails),
) -> AuthResponse:
    return AuthResponse(message=await emails.send_sign_in_code(bg, str(request.email)))


@router.post(
    '/sign-in-with-code',
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    description='Sign in using email and verification code',
)
async def sign_in_with_code(
    credentials: SignInCodeRequest = Body(..., description='Email and verification code'),
    otp: OTPService = Depends(otp_service),
) -> TokenResponse:
    return await otp.validate_and_signin_with_code(
        email=str(credentials.email),
        code=credentials.code,
    )


@router.post(
    '/send-reset-password-code',
    response_model=AuthResponse,
    status_code=status.HTTP_200_OK,
    description='Send password reset code to email',
)
async def send_reset_password_code(
    bg: BackgroundTasks,
    request: SendResetPasswordCodeRequest = Body(..., description='Email to send reset code to'),
    emails: AuthEmails = Depends(auth_emails),
) -> AuthResponse:
    return AuthResponse(message=await emails.send_reset_password_code(bg, str(request.email)))


@router.post(
    '/reset-password',
    response_model=AuthResponse,
    status_code=status.HTTP_200_OK,
    description='Reset password with verification code',
)
async def reset_password(
    request: ResetNewPasswordRequest = Body(..., description='Password reset details'),
    otp: OTPService = Depends(otp_service),
) -> AuthResponse:
    message = await otp.reset_password_with_code(
        email=str(request.email), code=request.code, new_password=request.new_password
    )
    return AuthResponse(message=message)


# ── CLI auth ─────────────────────────────────────────────────────────


@router.post(
    '/cli/token',
    response_model=CLITokenResponse,
    status_code=status.HTTP_200_OK,
    description='Exchange a portal token + org selection for an org-scoped CLI token',
)
async def create_cli_token(
    body: CLITokenRequest = Body(...),
    token: str = Depends(_oauth2),
    tokens: CLITokens = Depends(cli_tokens),
) -> CLITokenResponse:
    """Issue an org-scoped JWT for the CLI.

    The caller must already be authenticated (portal token) and pass the
    org_id they selected.  The endpoint verifies membership and returns a
    new token with the org baked in.
    """
    return await tokens.issue(token=token, org_id=body.org_id)
