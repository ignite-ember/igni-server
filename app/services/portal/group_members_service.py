"""Who is in which group: every person of an organisation is in exactly one."""

from __future__ import annotations

from app.api.base_v1.schema.portal.groups import (
    OrgGroupMemberList,
    OrgGroupMemberResponse,
    OrgGroupMembership,
    OrgGroupMembershipList,
    OrgGroupSummary,
)
from app.db.sql.group_members import GroupMembersDB
from app.db.sql.group_summaries import GroupSummariesDB
from app.db.sql.team import TeamDB
from app.db.sql.user import UserDB
from app.services.portal.default_group import DefaultGroup
from app.services.portal.group_access import GroupAccess
from app.services.portal.group_errors import NotAnOrgMember


class GroupMembersService:
    """Placing people in groups, and reading where they are."""

    def __init__(
        self,
        *,
        members: GroupMembersDB | None = None,
        team: TeamDB | None = None,
        users: UserDB | None = None,
        summaries: GroupSummariesDB | None = None,
        access: GroupAccess | None = None,
        default_group: DefaultGroup | None = None,
    ) -> None:
        self._members = members or GroupMembersDB()
        self._team = team or TeamDB()
        self._users = users or UserDB()
        self._summaries = summaries or GroupSummariesDB()
        self._access = access or GroupAccess()
        self._default_group = default_group or DefaultGroup(
            members=self._members, team=self._team, summaries=self._summaries
        )

    async def list_members(self, *, org_id: str, group_id: str, by_user_id: str | None = None) -> OrgGroupMemberList:
        """The people in a group of *org_id*, with who they are.

        When a user id is given, everybody in no group yet is put in
        Default first, so the Default group lists everyone it holds.
        """
        await self._access.in_org(org_id=org_id, group_id=group_id)
        if by_user_id:
            await self._default_group.place_everyone(org_id, by_user_id=by_user_id)
        members = []
        for row in await self._members.list_members(org_id, group_id):
            user = await self._users.get_by_user_id(row.user_id)
            members.append(
                OrgGroupMemberResponse.of(
                    row,
                    email=user.email if user else None,
                    full_name=user.full_name if user else None,
                )
            )
        return OrgGroupMemberList(members=members, total=len(members))

    async def add_member(self, *, org_id: str, group_id: str, user_id: str, by_user_id: str) -> OrgGroupMemberResponse:
        """Put a person in this group — which moves them out of the one they were in.

        Everybody is in exactly one group, so there is no "add" that is
        not a move, and nothing to conflict with: a new hire whose first
        sync already put them in Default is moved from there like
        anybody else. Placing somebody where they already are changes
        nothing. Admin-only, enforced at the endpoint.
        """
        await self._access.in_org(org_id=org_id, group_id=group_id)
        membership = await self._team.get_active_member(org_id, user_id)
        if not membership:
            raise NotAnOrgMember()

        member = await self._members.place(org_id, group_id=group_id, user_id=user_id, added_by_user_id=by_user_id)
        return OrgGroupMemberResponse.of(member, email=membership.invited_email or None, full_name=None)

    async def remove_member(self, *, org_id: str, group_id: str, user_id: str, by_user_id: str) -> None:
        """Take a person out of this group, back into the Default one. Refused on Default itself."""
        await self._access.in_org(org_id=org_id, group_id=group_id)
        await self._default_group.ensure_for_org(org_id, created_by_user_id=by_user_id)
        await self._members.return_to_default(org_id, group_id=group_id, user_id=user_id, by_user_id=by_user_id)

    async def memberships(self, *, org_id: str, by_user_id: str) -> OrgGroupMembershipList:
        """Every active person of the organisation and the one group they are in.

        What the group page shows next to each person it could move
        here. Everybody not yet placed is put in Default first, so the
        answer has a group for every one of them.
        """
        await self._default_group.place_everyone(org_id, by_user_id=by_user_id)
        return OrgGroupMembershipList(
            memberships=[
                OrgGroupMembership(**membership.model_dump()) for membership in await self._members.memberships(org_id)
            ]
        )

    async def get_user_group(self, *, org_id: str, user_id: str) -> OrgGroupSummary | None:
        """The group *user_id* belongs to, if that group is in *org_id*.

        Filtered on the way out rather than asked of the database,
        because "which group is this user in" is not org-scoped at the
        storage layer and a user could be in a group elsewhere — whose
        id and name are not this org's to learn.

        None rather than a refusal for a user outside the org: the
        route's shape is "no group", and that is the honest answer to
        give somebody who is not entitled to know the difference.
        """
        summary = await self._summaries.get_user_group_summary(user_id)
        if not summary or str(summary.org_id) != str(org_id):
            return None
        return OrgGroupSummary(**summary.model_dump())
