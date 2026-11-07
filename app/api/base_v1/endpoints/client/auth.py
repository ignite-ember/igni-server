from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, Field

from app.api.base_v1.schema.auth import (
    AuthResponse,
    ResetNewPasswordRequest,
    SendResetPasswordCodeRequest,
    SendSignInCodeRequest,
    SendSignUpCodeRequest,
    SignInCodeRequest,
    SignInRequest,
    SignUpRequest,
)
from app.db.redis.auth_handoff import AuthHandoffDB
from app.db.sql.organizations import OrganizationDB
from app.db.sql.team import TeamDB
from app.security.api_key_sessions import is_api_key_session, REFUSED as API_KEY_SESSION_REFUSED
from app.security.subject import resolve_existing_user_id
from app.services.auth.auth_service import AuthService
from app.services.auth.jwt_service import JWTService, TokenResponse
from app.services.auth.otp_service import CodeSendThrottled, OTPService
from app.services.email.delivery import EmailDelivery
from app.services.email.service import EmailService
from app.services.portal.group_extensions import GroupExtensions

_oauth2 = OAuth2PasswordBearer(tokenUrl='token')
_org_db = OrganizationDB()
_handoff = AuthHandoffDB()
_team_db = TeamDB()

router = APIRouter()

_delivery = EmailDelivery()


async def _send_code(
    bg: BackgroundTasks,
    *,
    generate,
    deliver,
    purpose: str,
    email: str,
) -> AuthResponse:
    """Issue a verification code and answer identically however it went.

    Three endpoints below do the same four things in the same order, and
    F112 found two ways that mattered to get wrong:

    * a **throttled** send must not be distinguishable from a delivered
      one. A 429 would say "somebody asked for a code for this address
      recently", which is an activity oracle on endpoints designed to
      reveal nothing about an address at all;
    * a **suppressed** send — the address already has an account, on the
      sign-up path — must not be distinguishable either. That difference
      was the enumeration oracle: `400 User already registered` against
      `200`.

    So there is one exit for all four outcomes: sent, throttled,
    suppressed, and no relay configured. The last one still differs in
    wording, because a deployment with no mail relay tells *everybody*
    the same thing and that is a property of the deployment rather than
    of the address.
    """
    try:
        result = await generate(email)
    except CodeSendThrottled:
        # Silent, and identical — including on a deployment with no mail
        # relay, which is the case the first version got wrong. It
        # returned "has been sent" while a *delivered* code on the same
        # deployment returns `no_relay_message()`, so the throttle was
        # distinguishable on exactly the air-gapped installs where codes
        # go to the log. Caught by the test asserting the two are
        # identical.
        #
        # The counter and the log line in code_throttle.py are how an
        # operator sees a refusal.
        if not _delivery.enabled:
            return AuthResponse(message=_delivery.no_relay_message())
        return AuthResponse(message=f'Verification code has been sent to {email}')

    if not _delivery.enabled:
        # No relay. The code goes to the log only if the operator turned
        # LOG_SIGN_IN_CODES on (F35) — and never into the response, which
        # any anonymous caller could request for any address.
        if not result.suppress_delivery:
            _delivery.record_undeliverable_code(purpose=purpose, email=result.email, code=result.code)
        return AuthResponse(message=_delivery.no_relay_message())

    if not result.suppress_delivery:
        bg.add_task(deliver, to_email=result.email, code=result.code)

    return AuthResponse(message=result.message)


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
) -> TokenResponse:
    token = await AuthService.sign_up(user_data)
    if _delivery.enabled:
        bg.add_task(EmailService().send_welcome, to_email=str(user_data.email), name=user_data.first_name)
    else:
        # Informational; nothing is lost that has to be replaced by hand.
        _delivery.note_undeliverable(purpose='welcome', email=str(user_data.email))
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
) -> AuthResponse:
    return await _send_code(
        bg,
        generate=lambda email: AuthService.send_sign_up_code(email=email),
        deliver=EmailService().send_sign_up_code,
        purpose='sign-up',
        email=str(request.email),
    )


@router.post(
    '/send-sign-in-code',
    response_model=AuthResponse,
    status_code=status.HTTP_200_OK,
    description='Send sign-in code to email',
)
async def send_sign_in_code(
    bg: BackgroundTasks,
    request: SendSignInCodeRequest = Body(..., description='Email to send sign-in code to'),
) -> AuthResponse:
    return await _send_code(
        bg,
        generate=lambda email: OTPService().generate_signin_code(email=email),
        deliver=EmailService().send_otp_code,
        purpose='sign-in',
        email=str(request.email),
    )


@router.post(
    '/sign-in-with-code',
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    description='Sign in using email and verification code',
)
async def sign_in_with_code(
    credentials: SignInCodeRequest = Body(..., description='Email and verification code'),
) -> TokenResponse:
    return await OTPService().validate_and_signin_with_code(
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
) -> AuthResponse:
    return await _send_code(
        bg,
        generate=lambda email: OTPService().generate_password_reset_code(email=email),
        deliver=EmailService().send_password_reset_code,
        purpose='password-reset',
        email=str(request.email),
    )


@router.post(
    '/reset-password',
    response_model=AuthResponse,
    status_code=status.HTTP_200_OK,
    description='Reset password with verification code',
)
async def reset_password(
    request: ResetNewPasswordRequest = Body(..., description='Password reset details'),
) -> AuthResponse:
    message = await OTPService().reset_password_with_code(
        email=str(request.email), code=request.code, new_password=request.new_password
    )
    return AuthResponse(message=message)


# ── CLI auth ─────────────────────────────────────────────────────────


class CLITokenRequest(BaseModel):
    org_id: str


class CLITokenResponse(TokenResponse):
    """The org-scoped token, plus a code safe to put in a URL.

    Two fields because there are two consumers with opposite
    constraints. A caller that already has this response has the token
    in a POST body, which is where a credential belongs. The **browser**
    doing the desktop handoff has to get it to a local HTTP server by
    navigating there, and a token in a navigation lands in history.

    So the browser gets `handoff_code` — the same single-use,
    30-second, `GETDEL`-redeemed code F107 built for the sign-in
    redirect — and exchanges it at `POST /v1/auth/exchange`. F124.
    """

    handoff_code: str = Field(
        description=(
            'Single-use code for the local callback. Valid for 30 seconds, redeemable once at '
            'POST /v1/auth/exchange. Use this rather than the token when the value has to travel '
            'through a URL.'
        )
    )


@router.post(
    '/cli/token',
    response_model=CLITokenResponse,
    status_code=status.HTTP_200_OK,
    description='Exchange a portal token + org selection for an org-scoped CLI token',
)
async def create_cli_token(
    body: CLITokenRequest = Body(...),
    token: str = Depends(_oauth2),
) -> CLITokenResponse:
    """Issue an org-scoped JWT for the CLI.

    The caller must already be authenticated (portal token) and pass the
    org_id they selected.  The endpoint verifies membership and returns a
    new token with the org baked in.
    """
    # Minting a fresh token for somebody who no longer exists would
    # hand out 30 more days on the strength of an old one. F122.
    user_id = await resolve_existing_user_id(token)
    # Nor 30 days on the strength of a 15-minute session an API key bought (§22).
    if is_api_key_session(token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=API_KEY_SESSION_REFUSED)

    membership = await _team_db.get_active_member(body.org_id, user_id)
    if not membership:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail='Not a member of this organization')

    org = await _org_db.get_by_id(body.org_id)
    org_name = str(org.display_name) if org else None

    group = await GroupExtensions().group_of(user_id, body.org_id)
    cli_token = JWTService().create_token(user_id, org_id=body.org_id, org_name=org_name, group=group)
    # Issued unconditionally rather than on request. A caller that does
    # not need it ignores one short string; a caller that needs it and
    # cannot get it puts the token in a URL instead, which is the
    # failure being removed.
    return CLITokenResponse(
        access_token=cli_token,
        handoff_code=await _handoff.issue(cli_token),
    )
