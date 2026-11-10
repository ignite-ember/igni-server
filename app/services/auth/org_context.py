"""Which person a session is, and which organisation they are acting in."""

from __future__ import annotations

from pydantic import BaseModel

from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.organizations import OrganizationDB
from app.db.sql.team import TeamDB
from app.security.membership_sessions import MembershipSessions
from app.security.subject import Subjects
from app.services.auth.auth_errors import InvalidSession, NotAMember
from app.services.auth.jwt_service import JWTService


class OrgContext(BaseModel):
    """A signed-in person and the organisation they act in."""

    user_id: str
    #: None only for a person in no organisation yet.
    org_id: str | None


class OrgContexts:
    """Resolves a session token to a person and an organisation they are a current member of."""

    def __init__(
        self,
        *,
        jwt: JWTService | None = None,
        subjects: Subjects | None = None,
        org_db: OrganizationDB | None = None,
        team_db: TeamDB | None = None,
    ) -> None:
        self._jwt = jwt or JWTService()
        self._subjects = subjects or Subjects(tokens=self._jwt)
        self._org_db = org_db or OrganizationDB()
        self._team_db = team_db or TeamDB()

    async def resolve(self, token: str, *, org_id: str | None = None) -> OrgContext:
        """Decode *token* and resolve its organisation, asserting active membership.

        *org_id* is an explicit organisation (from a header); without one
        the token's claim is used, then the person's oldest organisation.
        """
        claims = self._jwt.decode_token(token)
        user_id = claims.user_id
        if not user_id:
            raise InvalidSession()

        # The chat path. Without this a deleted user's token reached model
        # routing — and when no org resolves this returns without a
        # membership check at all, so the existence check is the only
        # authorisation left. F122. It also carries the revocation check
        # now (B5), which matters most here: this is the path that spends
        # the customer's GPU.
        await self._subjects.require_existing_user(user_id, issued_at=claims.issued_at)

        resolved_org_id = org_id or claims.org_id or await self._org_db.first_org_id(user_id)
        if not resolved_org_id:
            return OrgContext(user_id=user_id, org_id=None)

        membership = await self._active_membership(org_id=resolved_org_id, user_id=user_id)
        if claims.org_id == resolved_org_id:
            MembershipSessions.require_current(membership, claims.issued_at)
        return OrgContext(user_id=user_id, org_id=resolved_org_id)

    async def _active_membership(self, *, org_id: str, user_id: str) -> OrgMembership:
        """The membership, or `NotAMember`. Not cached.

        It used to carry ``@async_ttl_cache(ttl=600)`` and a docstring saying
        "cached for 10 minutes", and it was never cached at all: the decorator
        treats a ``None`` result as a miss (``if cached is not None``), and
        this returned ``None`` by design — it raised or it did not. So every
        call re-queried, and the cache filled with ``None`` entries nobody
        could read.

        Left uncached deliberately now that the choice is visible. Caching it
        properly would mean a removed or suspended member keeps chat access
        for up to ten minutes, on the one path in this product that spends
        money per request. One indexed lookup on ``(org_id, user_id)`` is a
        better trade than a ten-minute revocation window, and the previous
        behaviour was accidentally the safe one.
        """
        membership = await self._team_db.get_active_member(org_id, user_id)
        if not membership:
            raise NotAMember()
        return membership
