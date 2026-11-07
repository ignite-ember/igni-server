"""Which group a person is in, starting from the person alone.

Groups live in each organisation's own database, so the one question that
starts from a person rather than an organisation first asks the shared
plane which organisations they belong to, then asks each of those. This
class is that crossing, and emits no SQL of its own.
"""

from __future__ import annotations

from app.db.sql.group_members import GroupMembersDB
from app.db.sql.organizations import OrganizationDB
from app.db.sql.schemas.group import GroupSummary
from app.utils.singleton import SingletonMeta


class GroupSummariesDB(metaclass=SingletonMeta):
    """A person's group, across the organisations they belong to."""

    def __init__(
        self,
        *,
        organizations: OrganizationDB | None = None,
        members: GroupMembersDB | None = None,
    ) -> None:
        self._organizations = organizations or OrganizationDB()
        self._members = members or GroupMembersDB()

    async def get_active_user_group_summary(self, user_id: str, org_id: str | None = None) -> GroupSummary | None:
        """The group a user belongs to **if their membership still holds**.

        B8. `org_group_members` records a group membership and knows
        nothing about the organisation membership underneath it, so
        `get_user_group_summary` answers for somebody who has been
        deactivated. On the admin view of a member that is right — an
        administrator deciding whether to reactivate or remove needs to
        see where that person sits. On the paths that serve a person
        their *own* configuration it is a leak: the pack carries the
        organisation's agent prompts and its hook commands, which run
        through ``bash -c`` on the developer's machine.

        Two methods rather than a flag, because the flag's default is
        the thing that gets got wrong six months later, and the name is
        where the distinction belongs.

        `org_id` narrows the answer to that one organisation — the one the
        person's session is in — and to nothing if they are not an active
        member of it. A person in two answers for the one they are signed
        into, never for whichever comes first.
        """
        active = await self._organizations.member_org_ids(user_id, active_only=True)
        if org_id is not None:
            active = [org for org in active if str(org) == str(org_id)]
        return await self._first_group(user_id, active)

    async def get_user_group_summary(self, user_id: str) -> GroupSummary | None:
        """The group a user belongs to, or None.

        Says nothing about whether the organisation membership underneath
        is active — see :meth:`get_active_user_group_summary`, which is
        what the self-serve paths use.
        """
        return await self._first_group(user_id, await self._organizations.member_org_ids(user_id, active_only=False))

    async def _first_group(self, user_id: str, org_ids: list[str]) -> GroupSummary | None:
        """The person's group in the first of these organisations that has one."""
        for org_id in org_ids:
            group = await self._members.group_of(org_id, user_id)
            if group is not None:
                return group
        return None
