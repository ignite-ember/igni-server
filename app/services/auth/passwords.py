"""Hashing and checking the passwords people sign in with."""

from __future__ import annotations

import bcrypt

#: A bcrypt hash of a throwaway value, at the cost ``bcrypt.gensalt()`` uses.
#: Compared against when there is no real hash to check, so that a failed
#: sign-in costs the same whether or not the account exists. A constant
#: rather than one built on first use: nothing about it needs to be secret
#: or fresh, and startup should not pay bcrypt's deliberate slowness.
TIMING_HASH = b'$2b$12$oEyIQ/xZusRkisayFSzu5.CTJkxe5E0V2oQ3BVRQy03rspU9Vqd8C'


class Passwords:
    """The one place a user's password meets bcrypt."""

    def hash(self, password: str) -> str:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    def verify(self, password: str, hashed: str) -> bool:
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

    def absorb_timing(self, password: str) -> None:
        """Spend the same bcrypt time a real comparison would.

        Without this the status code stops being an oracle but the clock
        does not: a missing account short-circuits before bcrypt and
        answers in ~1 ms, while a wrong password pays the full cost. One
        comparison against a throwaway hash keeps both paths level.
        """
        bcrypt.checkpw(password.encode(), TIMING_HASH)
