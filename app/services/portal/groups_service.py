"""Portal group management: the groups of an organisation themselves.

Their people are `group_members_service`, their entries
`group_entries_service`, and what a CLI receives `group_pack_service`.
"""

from __future__ import annotations

from app.api.base_v1.schema.portal.groups import (
    CopyGroupRequest,
    CreateGroupRequest,
    OrgGroupDetail,
    OrgGroupList,
    OrgGroupResponse,
    UpdateGroupRequest,
)
from app.db.sql.group_entries import GroupEntriesDB
from app.db.sql.group_members import GroupMembersDB
from app.db.sql.groups import GroupsDB
from app.db.sql.models.org_group import OrgGroup
from app.db.sql.schemas.group import GroupSettings
from app.services.portal.default_group import DefaultGroup
from app.services.portal.group_access import GroupAccess
from app.services.portal.served_models import ServedModels


class GroupsService:
    """Creating, reading, changing and deleting an organisation's groups."""

    def __init__(
        self,
        *,
        groups: GroupsDB | None = None,
        members: GroupMembersDB | None = None,
        entries: GroupEntriesDB | None = None,
        access: GroupAccess | None = None,
        served: ServedModels | None = None,
        default_group: DefaultGroup | None = None,
    ) -> None:
        self._groups = groups or GroupsDB()
        self._members = members or GroupMembersDB()
        self._entries = entries or GroupEntriesDB()
        self._access = access or GroupAccess(groups=self._groups)
        self._served = served or ServedModels()
        self._default_group = default_group or DefaultGroup()

    async def list_groups(
        self,
        *,
        org_id: str,
        offset: int = 0,
        limit: int | None = None,
        seed_default_for_user_id: str | None = None,
    ) -> OrgGroupList:
        """Every group in the org, with its member and entry counts.

        Seeds the Default group first when a user id is given, and puts
        everybody who is in no group yet into it. This is where an admin
        goes looking for the group to start others from, so this is where
        it has to exist, holding the people it holds.
        """
        if seed_default_for_user_id:
            await self._default_group.place_everyone(org_id, by_user_id=seed_default_for_user_id)

        page = await self._groups.list_groups(org_id, offset=offset, limit=limit)
        groups = [await self._detail(org_id=org_id, group=group) for group in page.items]
        return OrgGroupList(groups=groups, total=page.total, offset=offset, limit=limit)

    async def create_group(self, *, org_id: str, body: CreateGroupRequest, by_user_id: str) -> OrgGroupResponse:
        """A new group, empty or copied from *body*'s template."""
        await self._access.refuse_taken_name(org_id=org_id, name=body.name)
        await self._served.refuse_unserved(body.default_model)

        settings = GroupSettings(
            name=body.name,
            description=body.description,
            default_model=body.default_model or None,
            theme=body.theme.to_column() if body.theme else None,
        )
        if body.template_group_id:
            group = await self._groups.copy_group(
                org_id, from_group_id=body.template_group_id, settings=settings, by_user_id=by_user_id
            )
        else:
            group = await self._groups.create_group(org_id, settings=settings, by_user_id=by_user_id)
        return OrgGroupResponse.of(group)

    async def copy_group(
        self, *, org_id: str, group_id: str, body: CopyGroupRequest, by_user_id: str
    ) -> OrgGroupResponse:
        """A new group that is a copy of this one: entries, extensions and settings, nobody in it.

        What a group holds is what its people get, so copying one is how
        to answer "the same as that team, plus this" without retyping
        it. The Default group — which holds what igni ships — copies like
        any other, and the copy is an ordinary, editable group.

        A copy, not a link. The new group is free to diverge, and the
        one it came from must not change under it later. People are not
        copied: each is in exactly one group, and stays where they are.
        """
        source = await self._access.in_org(org_id=org_id, group_id=group_id)
        await self._access.refuse_taken_name(org_id=org_id, name=body.name)
        settings = GroupSettings(
            name=body.name,
            description=body.description if body.description is not None else source.description,
            default_model=source.default_model,
            theme=source.theme,
        )
        group = await self._groups.copy_group(org_id, from_group_id=group_id, settings=settings, by_user_id=by_user_id)
        return OrgGroupResponse.of(group)

    async def get_group(self, *, org_id: str, group_id: str, by_user_id: str | None = None) -> OrgGroupDetail:
        """One group with its counts.

        The Default group's count is everybody nobody has put elsewhere,
        so when a user id is given they are placed before they are counted.
        """
        group = await self._access.in_org(org_id=org_id, group_id=group_id)
        if by_user_id and bool(group.is_default):
            await self._default_group.place_everyone(org_id, by_user_id=by_user_id)
        return await self._detail(org_id=org_id, group=group)

    async def update_group(self, *, org_id: str, group_id: str, body: UpdateGroupRequest) -> OrgGroupResponse:
        """Change what *body* supplies, and nothing else."""
        await self._access.writable(org_id=org_id, group_id=group_id)
        if body.name:
            await self._access.refuse_taken_name(org_id=org_id, name=body.name, except_id=group_id)
        await self._served.refuse_unserved(body.default_model)

        group = await self._groups.update_group(org_id, group_id=group_id, patch=body.to_patch())
        return OrgGroupResponse.of(group)

    async def delete_group(self, *, org_id: str, group_id: str, by_user_id: str) -> None:
        """Delete a group. Its people go back to the Default group, in the same transaction."""
        await self._access.writable(org_id=org_id, group_id=group_id)
        await self._default_group.ensure_for_org(org_id, created_by_user_id=by_user_id)
        await self._groups.delete_group(org_id, group_id=group_id)

    async def _detail(self, *, org_id: str, group: OrgGroup) -> OrgGroupDetail:
        members = await self._members.list_members(org_id, group.id)
        entries = await self._entries.list_entries(org_id, group.id)
        return OrgGroupDetail.of(group, member_count=len(members), entry_count=len(entries))
