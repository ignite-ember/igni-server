"""The person a token names: still there, still here, and signed in since any revocation.

Every authenticated request turns a token into a person through
`Subjects`, so these invariants hold in one place rather than per call
site (`tests/test_a_deleted_user_cannot_come_back.py` sweeps every route
to keep it that way):

- **The account exists.** A signed token outlives the account it names;
  deleting somebody ends their session here, not when the token expires.
- **The account is not deactivated.** Deprovisioning is one fact about a
  person, not about a membership: every account also has a personal
  organisation, so membership alone cannot express "has left".
- **The token postdates `users.tokens_valid_after`.** One watermark
  withdraws every session a person holds at once. A token without an
  `iat` cannot be shown to postdate it and is refused.

Organisation membership is *not* checked here: what counts as sufficient
membership differs per route, so it belongs to the route.

Every refusal carries the same wording as a bad signature (AC-10): the
holder learns their token stopped working, and nothing about why.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.db.sql.models.user import User
from app.db.sql.user import UserDB
from app.services.auth.jwt_service import JWTService

_REFUSAL = 'Invalid or expired token'


class Subjects:
    """Resolves a token, or a token's user id, to a person who may still act."""

    def __init__(self, *, users: UserDB | None = None, tokens: JWTService | None = None) -> None:
        self._users = users or UserDB()
        self._tokens = tokens or JWTService()

    @classmethod
    def default(cls) -> Subjects:
        """The instance a route gets from ``Depends``."""
        return cls()

    @staticmethod
    def refuse_if_deactivated(user: User | None) -> None:
        """Refuse a sign-in by somebody who has been deprovisioned, with 403.

        Sign-in does not pass through a token, so without this a person
        who had left could sign in again and be handed a fresh session.
        403 and a reason, unlike the token refusals: the credential was
        accepted and the *account* is not, so "invalid email or password"
        would send them to reset a password that works.
        """
        if user is not None and user.deactivated_at is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail='This account has been deactivated.',
            )

    async def resolve_existing_user_id(self, authorization: str) -> str:
        """Decode `authorization` and return the user id of a person who may still act, or 401.

        Decoding, existence and revocation in one call: a helper that only
        decodes looks complete and is half an authorisation check.
        """
        claims = self._tokens.decode_token(authorization)
        if not claims.user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_REFUSAL)
        await self.require_existing_user(claims.user_id, issued_at=claims.issued_at)
        return claims.user_id

    async def require_existing_user(self, user_id: str, *, issued_at: int | None) -> User:
        """The `users` row for `user_id`, or 401.

        Returns the row so the caller does not read it twice. `issued_at`
        is the token's `iat` in epoch seconds, keyword-only and required:
        an optional one would let a call site skip the revocation check
        while reading as though it had one.
        """
        user = await self._users.get_by_user_id(user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_REFUSAL)
        # Before the watermark: a deprovisioned person is refused whatever
        # their token's age, and reactivation does not re-admit old tokens.
        if user.deactivated_at is not None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_REFUSAL)
        if self._predates_revocation(user, issued_at):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_REFUSAL)
        return user

    @staticmethod
    def _predates_revocation(user: User, issued_at: int | None) -> bool:
        """Whether a token issued at `issued_at` is withdrawn by the user's watermark.

        Equal counts as withdrawn: `iat` is whole seconds, and the
        alternative leaves a session alive through the second it was ended in.
        """
        revoked_before = user.tokens_valid_after
        if revoked_before is None:
            return False
        return issued_at is None or issued_at <= revoked_before.timestamp()
