"""Which groups a request may reach, and which of those it may change."""

from __future__ import annotations

from app.db.sql.groups import GroupsDB
from app.db.sql.models.org_group import OrgGroup
from app.db.sql.schemas.group import GroupNameTaken, GroupNotFound
from app.services.portal.group_errors import GroupIsStandard


class GroupAccess:
    """The org-scoping and immutability rules every group path goes through."""

    def __init__(self, *, groups: GroupsDB | None = None) -> None:
        self._groups = groups or GroupsDB()

    async def in_org(self, *, org_id: str, group_id: str) -> OrgGroup:
        """The group, if it is in *org_id*.

        Every group, member and entry path is scoped to an organisation:
        a group id alone never reaches a group, so an admin of one org
        cannot read or edit another's by knowing the id.
        """
        group = await self._groups.get_group(org_id, group_id=group_id)
        if not group:
            raise GroupNotFound()
        return group

    async def writable(self, *, org_id: str, group_id: str) -> OrgGroup:
        """The group, if it is in *org_id* and may be changed.

        The Default group holds what igni ships and is what every other
        group is started from. Editing it would leave an org with no
        reference point and nothing to copy, so it is immutable.
        """
        group = await self.in_org(org_id=org_id, group_id=group_id)
        if bool(group.is_default):
            raise GroupIsStandard()
        return group

    async def refuse_taken_name(self, *, org_id: str, name: str, except_id: str | None = None) -> None:
        """Refuse a name another group in *org_id* already has.

        *except_id* is the group being renamed, which may keep its own
        name. The store's unique constraint answers the same way for the
        race this check-then-act cannot see.
        """
        existing = await self._groups.get_group_by_name(org_id, name=name)
        if existing and existing.id != except_id:
            raise GroupNameTaken(name)
