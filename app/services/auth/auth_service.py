"""Service layer for user authentication (sign-in, sign-up)."""

import logging

import bcrypt
from fastapi import HTTPException, status

from app.api.base_v1.schema.auth import SignInRequest, SignUpRequest
from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.organizations import OrganizationDB
from app.db.sql.schemas.org_domain import AdmissionRefused
from app.db.sql.team import TeamDB
from app.db.sql.user import UserDB
from app.security.account_domains import refuse_public_mail
from app.security.membership_sessions import MembershipSessions
from app.security.subject import refuse_if_deactivated, require_existing_user
from app.services.auth.jwt_service import JWTService, TokenResponse
from app.services.auth.otp_service import OTPResult, OTPService
from app.services.identity.account_refusals import AccountRefusals
from app.utils.internal_failure import internal_failure

logger = logging.getLogger(__name__)

_user_db = UserDB()
_team_db = TeamDB()
_org_db = OrganizationDB()


class AuthService:
    """Business logic for authentication flows."""

    @staticmethod
    def hash_password(password: str) -> str:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    @staticmethod
    def verify_password(password: str, hashed: str) -> bool:
        """True only if *password* matches a usable bcrypt *hashed*.

        ``bcrypt.checkpw`` raises ``ValueError: Invalid salt`` when the
        stored value is not a bcrypt hash, and ``''`` is exactly what
        ``UserDB.ensure_exists`` stores — so every account created through
        OIDC, OAuth or an OTP sign-up that never set a password had this
        raise straight out of the endpoint as a 500.

        An account with no password cannot be signed into with a password.
        That is a failed verification, not an error, so it returns False.
        Same shape as the super-admin hash fix (F23), where a value that
        was not a bcrypt hash produced a blind 500.
        """
        try:
            return bcrypt.checkpw(password.encode(), hashed.encode())
        except ValueError:
            return False

    #: Compared against when there is no real hash to check, so that a
    #: failed sign-in costs the same whether or not the account exists.
    #: Built once on first use rather than at import, because bcrypt is
    #: deliberately slow and startup should not pay for it.
    _dummy_hash: str | None = None

    @staticmethod
    def _absorb_timing(password: str) -> None:
        """Spend the same bcrypt time a real comparison would.

        Without this the status code stops being an oracle but the clock
        does not: a missing account short-circuits before bcrypt and
        answers in ~1 ms, while a wrong password pays the full cost. One
        comparison against a throwaway hash keeps both paths level.
        """
        if AuthService._dummy_hash is None:
            AuthService._dummy_hash = bcrypt.hashpw(b'x', bcrypt.gensalt()).decode()
        bcrypt.checkpw(password.encode(), AuthService._dummy_hash.encode())

    @staticmethod
    async def sign_in(credentials: SignInRequest) -> TokenResponse:
        """Verify credentials and return a JWT.

        Every failure answers 401 with the same body. A 500 here was an
        account-enumeration oracle: an unregistered address answered 401
        and a registered one with no password answered 500, so the status
        code alone told an attacker which addresses have accounts.
        """
        user = await _user_db.get_by_email(str(credentials.email))
        stored = str(user.hashed_password) if user else ''
        if not user or not AuthService.verify_password(credentials.password, stored):
            if not stored:
                AuthService._absorb_timing(credentials.password)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Invalid email or password',
            )
        refuse_if_deactivated(user)
        token = JWTService().create_token(str(user.user_id))
        await _user_db.note_sign_in(str(user.user_id))
        return TokenResponse(access_token=token)

    @staticmethod
    async def sign_up(user_data: SignUpRequest) -> TokenResponse:
        """Validate OTP, create user, and return a JWT."""
        await OTPService().validate_signup_code(email=str(user_data.email), code=user_data.code)

        full_name = f'{user_data.first_name} {user_data.last_name}'.strip() or None
        try:
            with AccountRefusals():
                user = await _user_db.create(
                    str(user_data.email),
                    hashed_password=AuthService.hash_password(user_data.password),
                    full_name=full_name,
                )
        except AdmissionRefused as exc:
            # An address this deployment does not take, said in words the person can act on.
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except HTTPException:
            # A duplicate or personal address, from `AccountRefusals`: the
            # caller's problem, and the response says so. Passed through.
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

        token = JWTService().create_token(str(user.user_id))
        await _user_db.note_sign_in(str(user.user_id))
        return TokenResponse(access_token=token)

    @staticmethod
    async def send_sign_up_code(email: str) -> OTPResult:
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

        A personal mail-provider address is refused here, before any
        of that. ``UserDB.create`` refuses it too and is the backstop
        that also covers the OAuth paths — but reaching only that one
        means sending somebody a verification code, waiting for them
        to go and find it, and telling them at the end that the
        address was never going to work.

        This does not reopen the oracle above. Whether an address is
        at gmail.com is visible to anyone who can read it; whether it
        has an account here is not. The two answers this can now give
        are keyed on the former and say nothing about the latter.
        """
        with AccountRefusals():
            refuse_public_mail(email)
        existing = await _user_db.get_by_email(email)
        result = await OTPService().generate_signup_code(email=email)

        if existing:
            # Deliberately not raising. The endpoint sends nothing when
            # `suppress_delivery` is set and returns the ordinary
            # message.
            result.suppress_delivery = True

        return result

    @staticmethod
    async def resolve_user_org(token: str, org_id: str | None = None) -> tuple[str, str | None]:
        """Decode JWT and resolve (user_id, org_id), asserting active membership.

        Args:
            token: JWT access token.
            org_id: Explicit org context (from header or token claim).

        Returns:
            (user_id, org_id) tuple. org_id may be None if no org context.
        """
        claims = JWTService().decode_token(token)
        user_id = claims.user_id
        if not user_id:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail='Invalid or expired token')

        # The chat path. Without this a deleted user's token reached model
        # routing — and when no org resolves this function returns without
        # a membership check at all, so the existence check is the only
        # authorisation left. F122. It also carries the revocation check
        # now (B5), which matters most here: this is the path that spends
        # the customer's GPU.
        await require_existing_user(user_id, issued_at=claims.issued_at)

        resolved_org_id = org_id or claims.org_id
        if not resolved_org_id:
            # Try to find the user's default org
            user_orgs = await _org_db.list_user_orgs(user_id)
            if user_orgs:
                resolved_org_id = str(user_orgs[0][0].id)

        if not resolved_org_id:
            return user_id, None

        membership = await _assert_active_member(resolved_org_id, user_id)
        if claims.org_id == resolved_org_id:
            MembershipSessions.require_current(membership, claims.issued_at)
        return user_id, resolved_org_id


async def _assert_active_member(org_id: str, user_id: str) -> OrgMembership:
    """Raise 403 unless the user is an active member. Not cached.

    It used to carry ``@async_ttl_cache(ttl=600)`` and a docstring saying
    "cached for 10 minutes", and it was never cached at all: the decorator
    treats a ``None`` result as a miss (``if cached is not None``), and
    this function returns ``None`` by design — it raises or it does not.
    So every call re-queried, and the cache filled with ``None`` entries
    nobody could read.

    Left uncached deliberately now that the choice is visible. Caching it
    properly would mean a removed or suspended member keeps chat access
    for up to ten minutes, on the one path in this product that spends
    money per request. One indexed lookup on ``(org_id, user_id)`` is a
    better trade than a ten-minute revocation window, and the previous
    behaviour was accidentally the safe one.
    """
    membership = await _team_db.get_active_member(org_id, user_id)
    if not membership:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail='Not a member of this organization')
    return membership
