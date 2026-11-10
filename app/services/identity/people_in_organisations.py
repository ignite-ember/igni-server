"""Who a user token says somebody is, checked fresh at every issue."""

from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel

from app.db.sql.organizations import OrganizationDB
from app.db.sql.team import TeamDB
from app.security.membership_sessions import MembershipSessions
from app.security.subject import Subjects
from app.services.auth.token_claims import CustomClaims
from app.services.identity.errors import OAuthError


class PersonInOrganisation(BaseModel):
    """A person, still allowed and still an active member of one organisation."""

    user_id: str
    email: str
    name: str
    org_id: str
    org_name: str
    org_role: str
    super_admin: bool

    def claims(self, *, azp: str | None = None) -> CustomClaims:
        """What a `user` token for this person carries; *azp* names the client it was issued to."""
        return CustomClaims(
            org=self.org_id,
            org_name=self.org_name,
            org_role=self.org_role,
            email=self.email,
            name=self.name,
            super_admin=self.super_admin,
            azp=azp,
        )


class PeopleInOrganisations:
    def __init__(
        self,
        *,
        subjects: Subjects | None = None,
        team: TeamDB | None = None,
        organizations: OrganizationDB | None = None,
    ) -> None:
        self._subjects = subjects or Subjects()
        self._team = team or TeamDB()
        self._organizations = organizations or OrganizationDB()

    async def check(self, user_id: str, org_id: str, *, issued_at: int | None) -> PersonInOrganisation:
        """The person, still allowed and still in the organisation, or `invalid_grant`."""
        try:
            user = await self._subjects.require_existing_user(user_id, issued_at=issued_at)
        except HTTPException as exc:
            raise OAuthError.invalid_grant('this person can no longer sign in') from exc
        membership = await self._team.get_active_member(org_id, user_id)
        organisation = await self._organizations.get_by_id(org_id)
        if membership is None or organisation is None:
            raise OAuthError.invalid_grant('this person is not an active member of that organisation')
        if MembershipSessions.predates(membership, issued_at):
            raise OAuthError.invalid_grant('this was signed in under an earlier membership of that organisation')
        return PersonInOrganisation(
            user_id=user_id,
            email=str(user.email or ''),
            name=str(user.full_name or ''),
            org_id=org_id,
            org_name=str(organisation.display_name or organisation.slug or ''),
            org_role=str(membership.role),
            super_admin=bool(user.is_super_admin),
        )
