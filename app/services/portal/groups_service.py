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
        self._access = access or GroupAccess(groups=self._groups)
        self._served = served or ServedModels()
        self._default_group = default_group or DefaultGroup(
            groups=self._groups, members=members or GroupMembersDB(), entries=entries or GroupEntriesDB()
        )

    async def list_groups(
        self, *, org_id: str, by_user_id: str, offset: int = 0, limit: int | None = None
    ) -> OrgGroupList:
        """Every group in the org with its counts, once everybody is in one (`DefaultGroup.place_everyone`)."""
        await self._default_group.place_everyone(org_id, by_user_id=by_user_id)
        page = await self._groups.list_groups(org_id, offset=offset, limit=limit)
        tallies = await self._groups.tallies(org_id, group_ids=[group.id for group in page.items])
        groups = [OrgGroupDetail.of(group, tallies[group.id]) for group in page.items]
        return OrgGroupList(groups=groups, total=page.total, offset=offset, limit=limit)

    async def create_group(self, *, org_id: str, body: CreateGroupRequest, by_user_id: str) -> OrgGroupResponse:
        """A new group, empty or copied from *body*'s template.

        The served check applies to a model the request asks for; a copy
        (`copy_group`) inherits one that was checked when it was set.
        """
        await self._access.refuse_taken_name(org_id=org_id, name=body.name)
        await self._served.refuse_unserved(body.default_model)
        return await self._make(
            org_id=org_id, settings=body.to_settings(), from_group_id=body.template_group_id, by_user_id=by_user_id
        )

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
        return await self._make(
            org_id=org_id, settings=body.to_settings(source), from_group_id=group_id, by_user_id=by_user_id
        )

    async def get_group(self, *, org_id: str, group_id: str, by_user_id: str) -> OrgGroupDetail:
        """One group with its counts; the Default group's people are placed before they are counted."""
        group = await self._access.in_org(org_id=org_id, group_id=group_id)
        if group.is_default:
            await self._default_group.place_everyone(org_id, by_user_id=by_user_id)
        tallies = await self._groups.tallies(org_id, group_ids=[group.id])
        return OrgGroupDetail.of(group, tallies[group.id])

    async def update_group(self, *, org_id: str, group_id: str, body: UpdateGroupRequest) -> OrgGroupResponse:
        """Change what *body* supplies, and nothing else."""
        await self._access.writable(org_id=org_id, group_id=group_id)
        if body.name:
            await self._access.refuse_taken_name(org_id=org_id, name=body.name, except_id=group_id)
        await self._served.refuse_unserved(body.default_model)

        group = await self._groups.update_group(org_id, group_id=group_id, patch=body.to_patch())
        return OrgGroupResponse.of(group)

    async def delete_group(self, *, org_id: str, group_id: str, by_user_id: str) -> None:
        """Delete a group. Its people go back to the Default group, which must exist before the transaction."""
        await self._access.writable(org_id=org_id, group_id=group_id)
        await self._default_group.ensure_for_org(org_id, created_by_user_id=by_user_id)
        await self._groups.delete_group(org_id, group_id=group_id)

    async def _make(
        self, *, org_id: str, settings: GroupSettings, from_group_id: str | None, by_user_id: str
    ) -> OrgGroupResponse:
        """The one place a group is made: a copy of *from_group_id* when given, empty otherwise."""
        if from_group_id:
            group = await self._groups.copy_group(
                org_id, from_group_id=from_group_id, settings=settings, by_user_id=by_user_id
            )
        else:
            group = await self._groups.create_group(org_id, settings=settings, by_user_id=by_user_id)
        return OrgGroupResponse.of(group)
