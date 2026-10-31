"""The person a token names has to still exist.

F122. Two places turned a token into a user id — `portal_auth` for
everything under `/v1/portal`, and `AuthService.resolve_user_org` for the
chat path — and **neither required the user to be there**. A token is
signed and carries a 30-day expiry, so deleting somebody's account did
not end their session. Demonstrated:

    user gone-05091c deleted; token still in hand
      resolve_user_org        -> ('gone-05091c', None)
      POST /chat/completions  -> 404 "Model 'x' is not served"

A 404 about models means authorisation *passed* and the request reached
routing. With a model name this deployment serves, it would have been
served — a deleted developer spending the customer's GPU.

The portal side was worse than passing: with no `X-Org-Id` header,
`get_or_create_personal_org` **created** an organisation for the deleted
user id and returned them as its `admin`. So the first request after
offboarding did not merely succeed, it provisioned fresh state for
somebody who no longer existed.

## Why this lives in one place

Fixing two call sites independently is how the third one appears. Both go
through here now, and
`tests/test_a_deleted_user_cannot_come_back.py` sweeps **every**
authenticated route from the live router rather than trusting that two
were all there were — the F114 mistake, where an audit policy named three
prefixes while the interesting routes sat under a fourth.

## And now: whether the token predates a revocation

B5 built DEC-10's recommendation here, because this is already the one
place every authenticated request turns a token into a person — and it
already reads the row the answer lives on, so the check costs nothing.

`users.tokens_valid_after` is a single timestamp. A token whose `iat`
is older is refused. That withdraws *every* session a person holds at
once, which is the question an operator asks ("assume everything they
hold is compromised") rather than the one a deny list answers ("revoke
this string").

`issued_at` is a **required** argument, not an optional one. An
optional one would let a call site forget and get no check while
reading as though it had one, which is exactly what this module's
history is about: `JwtService.resolve_user_id` was removed because its
whole job was half of an authorisation check.

Nor does this check organisation membership: that is the caller's job,
because what counts as sufficient membership differs per route.

## And whether they still work here

Deprovisioning is *not* the membership question above. "May this
person act in this organisation, in this role, on this route" differs
per route and belongs to the route. "Has this person left" does not:
it is one fact about a person, with one answer, and it is the same
shape as the revocation beside it.

It is here because it was nowhere. Deactivating a member set a status
on their `org_memberships` row, which only the portal's own dependency
read — so a deactivated person kept their documents, their
notifications and their ability to publish, measured against a running
server, for the thirty days their token had left. Membership could not
have fixed it either: every account is given a personal organisation
at sign-up, so the company membership going inactive left an active
one beside it, and documents are owned per user rather than per
organisation.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.db.sql.user import UserDB
from app.services.auth.jwt_service import JWTService

_user_db = UserDB()

# The same wording as a bad signature or a missing claim, deliberately.
# A caller learns that their token no longer works, which they need to
# know, and nothing about why — AC-10's rule, applied to the one case
# where the reason is "you were removed".
_REFUSAL = 'Invalid or expired token'


def refuse_if_deactivated(user) -> None:
    """Refuse a sign-in by somebody who has been deprovisioned.

    The door above only sees tokens. Without this, a person who had
    left could sign in again — with their password, their one-time
    code or their identity provider — and be handed a fresh thirty
    days, which would make deprovisioning a thing that waits for a
    token to expire and is then undone by a login page.

    403 rather than 401, and unlike the door this one says why: the
    credential was accepted and the *account* is not, so "invalid
    email or password" would send somebody to reset a password that
    is working fine. Nothing is disclosed that the operator who
    deactivated them did not already decide to tell them.
    """
    if user is not None and getattr(user, 'deactivated_at', None) is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='This account has been deactivated.',
        )


async def resolve_existing_user_id(authorization: str) -> str:
    """Decode `authorization` and return the user id, or 401.

    The pairing that matters: decoding, existence and revocation
    together, in one call, because the two sites that did only the first
    part both looked complete. `JwtService.resolve_user_id` used to be
    the convenient way to do a fraction of this and has been removed — a
    helper whose whole job is to produce an unverified subject is a trap
    with a reassuring name.
    """
    claims = JWTService().decode_token(authorization)
    if not claims.user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_REFUSAL)
    await require_existing_user(claims.user_id, issued_at=claims.issued_at)
    return claims.user_id


async def require_existing_user(user_id: str, *, issued_at: int | None):
    """The `users` row for `user_id`, or 401.

    Returns the row so the caller does not read it twice: every call
    site needs the email and name from it anyway, and a second lookup
    would be a second chance for the two to disagree.

    `issued_at` is the token's `iat` in epoch seconds. Keyword-only and
    required: a caller that has not got one has not decoded a token, and
    should not be asking this question.
    """
    local_user = await _user_db.get_by_user_id(user_id)
    if local_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_REFUSAL)

    # Left the company. Checked before the watermark because it is the
    # stronger statement: a deprovisioned person is refused whatever
    # their token's age, and a reactivation does not re-admit the
    # tokens they held before they went.
    if local_user.deactivated_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_REFUSAL)

    revoked_before = local_user.tokens_valid_after
    if revoked_before is None:
        return local_user

    if issued_at is None:
        # A token with no `iat` cannot be compared against a revocation,
        # so it cannot be shown to postdate one. Refusing is the only
        # answer that keeps "revoked" meaning revoked; `create_token`
        # has always set `iat`, so this is a malformed or foreign token.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_REFUSAL)

    # `iat` is whole seconds, so a token minted in the same second as the
    # revocation compares equal. Treat equal as revoked: the alternative
    # leaves a one-second window in which a session survives the click
    # that was meant to end it.
    if issued_at <= revoked_before.timestamp():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_REFUSAL)

    return local_user
