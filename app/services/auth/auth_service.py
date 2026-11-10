"""Signing in and signing up with an email address and a password.

Each refusal is a typed error and this module decides no status code:
the router turns them into HTTP through `AuthRefusals` and
`AccountRefusals`.
"""

from __future__ import annotations

import logging

from app.api.base_v1.schema.auth import SignInRequest, SignUpRequest
from app.db.sql.schemas.account_errors import AccountConflict, AccountNotFound, PersonalAddress
from app.db.sql.schemas.org_domain import AdmissionRefused
from app.db.sql.user import UserDB
from app.security.account_domains import refuse_public_mail
from app.security.subject import Subjects
from app.services.auth.auth_errors import InvalidCredentials
from app.services.auth.jwt_service import JWTService, TokenResponse
from app.services.auth.otp_service import OTPResult, OTPService
from app.services.auth.passwords import Passwords
from app.utils.internal_failure import internal_failure

logger = logging.getLogger(__name__)

#: What the account store refuses with: the caller's to fix, so passed through as is.
_ACCOUNT_REFUSALS = (AdmissionRefused, AccountConflict, AccountNotFound, PersonalAddress)


class AuthService:
    """Business logic for the password sign-in and sign-up flows."""

    def __init__(
        self,
        *,
        users: UserDB | None = None,
        passwords: Passwords | None = None,
        jwt: JWTService | None = None,
        otp: OTPService | None = None,
    ) -> None:
        self._users = users or UserDB()
        self._passwords = passwords or Passwords()
        self._jwt = jwt or JWTService()
        self._otp = otp or OTPService()

    async def sign_in(self, credentials: SignInRequest) -> TokenResponse:
        """Verify credentials and return a JWT.

        Every failure is the same `InvalidCredentials`. A 500 here was an
        account-enumeration oracle: an unregistered address answered 401
        and a registered one with no password answered 500, so the status
        code alone told an attacker which addresses have accounts.
        """
        user = await self._users.get_by_email(str(credentials.email))
        stored = str(user.hashed_password) if user else ''
        if not user or not self._passwords.verify(credentials.password, stored):
            if not stored:
                self._passwords.absorb_timing(credentials.password)
            raise InvalidCredentials()
        Subjects.refuse_if_deactivated(user)
        return await self._session_for(str(user.user_id))

    async def sign_up(self, user_data: SignUpRequest) -> TokenResponse:
        """Validate OTP, create user, and return a JWT."""
        await self._otp.validate_signup_code(email=str(user_data.email), code=user_data.code)

        full_name = f'{user_data.first_name} {user_data.last_name}'.strip() or None
        try:
            user = await self._users.create(
                str(user_data.email),
                hashed_password=self._passwords.hash(user_data.password),
                full_name=full_name,
            )
        except _ACCOUNT_REFUSALS:
            raise
        except Exception as exc:
            # Everything else is ours. This returned `str(exc)` with a
            # 400 — so an anonymous caller got the failing SQL, every
            # column of `users`, and a 400 blaming them for a fault that
            # was not theirs. F120.
            raise internal_failure(exc, doing='creating the account', logger=logger) from exc

        # Already in the organisation that owns their address's domain,
        # with any pending invitation to it claimed: `UserDB.create` did
        # both, in the same transaction as the account.
        return await self._session_for(str(user.user_id))

    async def send_sign_up_code(self, email: str) -> OTPResult:
        """Generate a sign-up code, or pretend to for a known address.

        This used to answer `400 User already registered` when the
        address had an account and `200` when it did not — an
        unauthenticated, unlimited account-enumeration oracle, and one
        that contradicted the comment in the endpoint file promising the
        response "is identical whether or not the address belongs to a
        real account". F112. Same class of bug F44 closed on the sign-in
        path, still open here.

        Now both answer identically. A code is still generated and
        stored for a known address — the work is done either way, so the
        two paths do not differ in timing — and simply not sent. It
        cannot be used: `sign_up` refuses with a 409 when the account
        exists, after the code has already been validated, so possessing
        one buys nothing.

        A personal mail-provider address is refused here, with
        `PersonalAddress`, before any of that. ``UserDB.create`` refuses
        it too and is the backstop that also covers the OAuth paths — but
        reaching only that one means sending somebody a verification
        code, waiting for them to go and find it, and telling them at the
        end that the address was never going to work.

        This does not reopen the oracle above. Whether an address is
        at gmail.com is visible to anyone who can read it; whether it
        has an account here is not. The two answers this can now give
        are keyed on the former and say nothing about the latter.
        """
        refuse_public_mail(email)
        existing = await self._users.get_by_email(email)
        result = await self._otp.generate_signup_code(email=email)

        if existing:
            # Deliberately not raising. The endpoint sends nothing when
            # `suppress_delivery` is set and returns the ordinary
            # message.
            result.suppress_delivery = True

        return result

    async def _session_for(self, user_id: str) -> TokenResponse:
        """A fresh session for *user_id*, noted as a sign-in."""
        token = self._jwt.create_token(user_id)
        await self._users.note_sign_in(user_id)
        return TokenResponse(access_token=token)


def auth_service() -> AuthService:
    """The FastAPI dependency; tests replace it through `app.dependency_overrides`."""
    return AuthService()
