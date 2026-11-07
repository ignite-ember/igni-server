"""OTP service — generates, stores (Redis), and validates one-time codes.

All OTP state is managed locally in igni's Redis.

Three methods used to generate a code — sign-up, sign-in, password
reset — and they differed in two things: the key prefix and the final
message. Everything else, including a seven-line comment about why a
pending code is resent rather than rotated, was reproduced verbatim
three times. The purpose is a parameter now, and `OtpPurpose` is where
the two differences live. On a path that signs people in and resets
passwords, three copies of a security rule is three places to fix it
and two places to forget.

## What Redis holds (F151)

The code encrypted under ``SECRET_ENCRYPTION_KEY``, not the code. A six-
digit code is a million candidates: stored as an unkeyed hash, a copy of
Redis would give it up in a second, and stored as given it gives it up
in none. It is encrypted rather than HMAC'd because a pending code is
*resent*, not rotated (see ``generate_code``), so the server needs it
back. ``_pending_code`` is the one place that reads it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging
import secrets
import string

import bcrypt
from fastapi import HTTPException, status
import redis.asyncio as aioredis

from app.config import settings
from app.db.redis.sealed import seal, unseal
from app.db.sql.user import UserDB
from app.security.subject import refuse_if_deactivated
from app.services.auth.code_throttle import CodeThrottle
from app.services.auth.jwt_service import JWTService, TokenResponse
from app.services.secrets import SecretDecryptError
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)

_OTP_TTL_SECONDS = 600  # 10 minutes


#: How many wrong guesses a code tolerates before it is thrown away.
#:
#: A six-digit code is a million possibilities and lives for ten minutes,
#: and there was no counter at all: 300 wrong guesses in a row all
#: answered 401 and the real code still worked afterwards, verified
#: against the service. With no rate limiting anywhere in this product —
#: a deliberate decision elsewhere — that window is enough to walk a
#: large fraction of the space.
#:
#: Five, then the code is deleted rather than the account locked. Locking
#: the account would hand an attacker a denial-of-service against any
#: address they know; deleting the code costs the real user one more
#: email, and requesting that email is throttled by the mail path.
_MAX_ATTEMPTS = 5


def _generate_code() -> str:
    """Generate a random 6-digit OTP code.

    ``secrets`` rather than ``random``: this code signs somebody in and
    resets their password. ``random`` is the Mersenne Twister, whose state
    is recoverable from enough outputs — and the outputs here are handed
    to whoever asks for a code, so an attacker can collect them for their
    own address and then predict somebody else's.
    """
    return ''.join(secrets.choice(string.digits) for _ in range(6))


@dataclass
class OTPResult:
    """Result of OTP generation — carries the code so the caller can schedule email delivery."""

    message: str
    code: str
    email: str
    #: Generated, stored, and deliberately not delivered.
    #:
    #: Set when the address already has an account on the sign-up path.
    #: The work is done either way so the two branches do not differ in
    #: timing, and the caller returns the ordinary message — the account
    #: -enumeration oracle F112 closed was the *difference* between the
    #: two responses, not the code itself.
    suppress_delivery: bool = False


class CodeSendThrottled(RuntimeError):
    """A code was not generated because a send limit was reached.

    Carries the address so the caller can shape an identical response,
    and is deliberately *not* an ``HTTPException``: the endpoints answer
    200 with the same message a sent code produces. A 429 here would say
    "somebody asked for a code for this address recently", which is an
    activity oracle on endpoints designed to reveal nothing about
    whether an address exists.
    """

    def __init__(self, email: str) -> None:
        super().__init__('verification code send was throttled')
        self.email = email


class OtpPurpose(StrEnum):
    """What a code is for — and the only thing the three paths differ by.

    The value is the Redis key segment, so it is the same string the
    keys have always used and no stored code changes meaning.
    """

    SIGNUP = 'signup'
    SIGNIN = 'signin'
    RESET = 'reset'
    #: Proving a company address before the bootstrap account moves onto it.
    ADDRESS = 'address'

    def sent_message(self, email: str) -> str:
        """What the caller is told, which is the same whether a code was
        just made, resent, or silently withheld.

        Password reset says so; the other two are deliberately identical
        to each other and to the throttled and already-registered
        responses. The enumeration oracle F112 closed was the
        *difference* between those answers.
        """
        kind = 'Password reset code' if self is OtpPurpose.RESET else 'Verification code'
        return f'{kind} has been sent to {email}'


class OTPService(metaclass=SingletonMeta):
    """Issuing and checking one-time codes.

    Was five `@staticmethod`s over two module-level instances — a
    namespace, not an object, with nothing injectable. `UserDB` and
    `CodeThrottle` arrive through `__init__` now, so a caller can
    substitute either without reaching into the module.

    Redis is still opened per call rather than pooled. That is the
    house pattern — `auth_handoff`, `oauth_state`, `code_throttle` and
    `document_room` all do the same — and changing the connection
    lifetime of the sign-in path is not a thing to do as a side effect
    of removing duplication.
    """

    def __init__(self, *, users: UserDB | None = None, throttle: CodeThrottle | None = None) -> None:
        self._users = users or UserDB()
        self._throttle = throttle or CodeThrottle()

    @staticmethod
    def _redis() -> aioredis.Redis:  # type: ignore[type-arg]
        """Async Redis client for OTP storage."""
        return aioredis.from_url(settings.redis_url, decode_responses=True)  # type: ignore[no-any-return]

    @staticmethod
    async def _pending_code(r: aioredis.Redis, purpose: OtpPurpose, email: str) -> str | None:  # type: ignore[type-arg]
        """The live code for this purpose and address, decrypted. ``None`` if there is none.

        One that will not decrypt — sealed under a key since removed, or
        stored in plaintext before F151 — is deleted with its counter and
        reported as absent: the user asks for another, which is what they
        would do with an expired one.
        """
        stored = await r.get(f'otp:{purpose}:{email}')
        if not stored:
            return None
        try:
            return unseal(str(stored))
        except SecretDecryptError:
            logger.error('A pending %s code will not decrypt and was discarded', purpose)
            await r.delete(f'otp:{purpose}:{email}', f'otp:attempts:{purpose}:{email}')
            return None

    async def _check_code(self, purpose: OtpPurpose, email: str, code: str) -> bool:
        """Whether *code* is the live code for this purpose, counting failures.

        On success the code and its counter are consumed. On the fifth
        failure the code is deleted, so the attacker has to make the real
        user request another one.

        Compared with ``compare_digest``: a six-digit code over a network
        leaks little through timing, but the comparison is free either way
        and the habit is worth keeping where credentials are involved.

        A method rather than a free function taking the client first —
        it opens and closes its own connection now, which is also what
        removes the `aclose()` written twice at each of the three call
        sites.
        """
        code_key = f'otp:{purpose}:{email}'
        attempts_key = f'otp:attempts:{purpose}:{email}'

        r = self._redis()
        try:
            stored = await self._pending_code(r, purpose, email)
            if stored and secrets.compare_digest(stored, str(code)):
                await r.delete(code_key, attempts_key)
                return True

            if stored:
                failures = await r.incr(attempts_key)
                # Bound the counter's life to the code's, so a stale counter
                # cannot outlive the code and reject the next one.
                await r.expire(attempts_key, _OTP_TTL_SECONDS)
                if failures >= _MAX_ATTEMPTS:
                    await r.delete(code_key, attempts_key)
            return False
        finally:
            await r.aclose()

    async def _require_code(self, purpose: OtpPurpose, email: str, code: str) -> None:
        """Consume the code or refuse. One 401, said once.

        The three validators each opened a client, called the checker,
        and called `aclose()` on both the raise and the fall-through —
        so anything added between the check and the close would have
        leaked the connection on the failure path.
        """
        if not await self._check_code(purpose, email, code):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Invalid or expired verification code',
            )

    async def generate_code(self, purpose: OtpPurpose, email: str) -> OTPResult:
        """Generate and store a code, or resend the pending one.

        Throttled: an anonymous caller could make this deployment send
        mail to any address as often as they asked, which griefs the
        mailbox owner and puts the customer's sending reputation on the
        line. Guarded here rather than in the endpoint so a new caller
        cannot forget. See app/services/auth/code_throttle.py; F112.
        """
        r = self._redis()
        try:
            pending = await self._pending_code(r, purpose, email)

            if not await self._throttle.allow(purpose=str(purpose), email=email, code_pending=bool(pending)):
                raise CodeSendThrottled(email)

            if pending:
                # A resend of the code they already have — never a new
                # one. Rotating it is what let an anonymous caller keep
                # a real user from ever completing a sign-in, and the
                # attempt allowance is deliberately *not* reset here:
                # otherwise a flood of resends would reset the guessing
                # counter indefinitely.
                return OTPResult(message=purpose.sent_message(email), code=pending, email=email)

            code = _generate_code()
            await r.set(f'otp:{purpose}:{email}', seal(code), ex=_OTP_TTL_SECONDS)
            # A fresh code starts with a fresh allowance, or an earlier
            # burst of failures would kill it on arrival.
            await r.delete(f'otp:attempts:{purpose}:{email}')
        finally:
            await r.aclose()
        return OTPResult(message=purpose.sent_message(email), code=code, email=email)

    async def generate_signup_code(self, email: str) -> OTPResult:
        return await self.generate_code(OtpPurpose.SIGNUP, email)

    async def generate_signin_code(self, email: str) -> OTPResult:
        return await self.generate_code(OtpPurpose.SIGNIN, email)

    async def generate_password_reset_code(self, email: str) -> OTPResult:
        return await self.generate_code(OtpPurpose.RESET, email)

    async def generate_address_code(self, email: str) -> OTPResult:
        return await self.generate_code(OtpPurpose.ADDRESS, email)

    async def validate_address_code(self, email: str, code: str) -> None:
        """Validate the code proving a new address. Raises HTTPException if invalid."""
        await self._require_code(OtpPurpose.ADDRESS, email, code)

    async def validate_signup_code(self, email: str, code: str) -> None:
        """Validate OTP code for sign-up. Raises HTTPException if invalid."""
        await self._require_code(OtpPurpose.SIGNUP, email, code)

    async def validate_and_signin_with_code(self, email: str, code: str) -> TokenResponse:
        """Validate OTP code and return authentication token."""
        await self._require_code(OtpPurpose.SIGNIN, email, code)

        user = await self._users.get_by_email(email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='User not found',
            )

        refuse_if_deactivated(user)
        token = JWTService().create_token(str(user.user_id))
        await self._users.note_sign_in(str(user.user_id))
        return TokenResponse(access_token=token)

    async def reset_password_with_code(self, email: str, code: str, new_password: str) -> str:
        """Validate OTP code and reset user password."""
        await self._require_code(OtpPurpose.RESET, email, code)

        user = await self._users.get_by_email(email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='User not found',
            )

        hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
        await self._users.update_password(user_id=str(user.user_id), hashed_password=hashed)
        return 'Password has been reset successfully. Please sign in with your new password.'


def otp_service() -> OTPService:
    """The FastAPI dependency; tests replace it through `app.dependency_overrides`."""
    return OTPService()
