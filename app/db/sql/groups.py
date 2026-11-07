"""An organisation's groups, as one aggregate with their members, entries and extensions.

Groups live in the organisation's own database, so every method names the
organisation. The rows a group owns belong to their own classes
(`GroupMembersDB`, `GroupEntriesDB`, `GroupExtensionsDB`); this one opens
a `GroupTransaction` when a change has to reach several of them at once,
and says what the change means in terms of it. Clearing a deleted account
out of the groups is `GroupPersonErasureDB`'s.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from app.db.sql.group_entries import GroupEntriesDB
from app.db.sql.group_extensions import GroupExtensionsDB
from app.db.sql.group_members import GroupMembersDB
from app.db.sql.group_transaction import GroupAggregate
from app.db.sql.models.org_group import OrgGroup
from app.db.sql.schemas.group import GroupNotFound, GroupPage, GroupPatch, GroupSettings, TemplateGroupNotFound
from app.db.sql.tenancy import TenantSessions


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class GroupsDB(GroupAggregate):
    """Org groups, scoped to an organisation.

    Not a singleton (see `GroupAggregate`): `GroupsDB(extensions=fake)`
    really uses the fake. The clock is read once per operation, so a
    copy's group, entries and extensions share one timestamp.
    """

    def __init__(
        self,
        *,
        sessions: TenantSessions | None = None,
        members: GroupMembersDB | None = None,
        entries: GroupEntriesDB | None = None,
        extensions: GroupExtensionsDB | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(sessions=sessions, members=members, entries=entries, extensions=extensions)
        self._clock = clock or _utc_now

    async def list_groups(self, org_id: str, *, offset: int = 0, limit: int | None = None) -> GroupPage:
        """The organisation's groups, oldest first.

        An `offset` of 0 and a `limit` of 0 or None both mean "not
        applied", so `limit=0` returns every group rather than none.
        """
        async with self._transaction(org_id) as tx:
            total = await tx.count()
            return GroupPage(items=await tx.page(offset=offset, limit=limit), total=total)

    async def get_group(self, org_id: str, *, group_id: str) -> OrgGroup | None:
        async with self._transaction(org_id) as tx:
            return await tx.group(group_id=group_id)

    async def get_group_by_name(self, org_id: str, *, name: str) -> OrgGroup | None:
        async with self._transaction(org_id) as tx:
            return await tx.group_named(name=name)

    async def create_group(
        self, org_id: str, *, settings: GroupSettings, by_user_id: str | None, is_default: bool = False
    ) -> OrgGroup:
        """A new, empty group. `GroupNameTaken` when the organisation has one by that name."""
        async with self._transaction(org_id) as tx:
            group = settings.new_group(
                org_id=org_id, by_user_id=by_user_id, is_default=is_default, vm_profile_id=None, at=self._clock()
            )
            await tx.add(group)
            return group

    async def update_group(self, org_id: str, *, group_id: str, patch: GroupPatch) -> OrgGroup:
        """Apply the fields the patch supplies. `GroupNotFound` when there is no such group."""
        async with self._transaction(org_id) as tx:
            row = await tx.group(group_id=group_id)
            if row is None:
                raise GroupNotFound()
            patch.apply_to(row, at=self._clock())
            await tx.flush()
            return row

    async def delete_group(self, org_id: str, *, group_id: str) -> None:
        """Delete a group; its people go back to the Default group in the same transaction.

        The membership rows cascade with the group, so without the move
        everybody in it would be in no group at all until something
        happened to ask — and a person is always in exactly one.
        `GroupNotFound` when there is no such group, `NoDefaultGroup`
        when there is nowhere to move them.
        """
        async with self._transaction(org_id) as tx:
            row = await tx.group(group_id=group_id)
            if row is None:
                raise GroupNotFound()
            await tx.return_everyone_in(group_id=group_id)
            await tx.remove(row)

    async def copy_group(
        self, org_id: str, *, from_group_id: str, settings: GroupSettings, by_user_id: str | None
    ) -> OrgGroup:
        """A new group holding a copy of another's entries and extensions — and nobody.

        One transaction: a copy is all of it or none of it, never a
        group that exists with half its entries. The settings it is
        made with are the caller's, so "copy that group" and "start a
        new group from that one" are the same operation with different
        names on the front. The VM profile is a setting like the theme,
        but it is the source's: the copy's people get the same microVM.
        People are not copied: a person is in one group, and the copy
        starts empty of them.
        """
        async with self._transaction(org_id) as tx:
            source = await tx.group(group_id=from_group_id)
            if source is None:
                raise TemplateGroupNotFound()
            now = self._clock()
            group = settings.new_group(
                org_id=org_id, by_user_id=by_user_id, is_default=False, vm_profile_id=source.vm_profile_id, at=now
            )
            await tx.add(group)
            await tx.copy_contents(from_group_id=from_group_id, to_group_id=group.id, by_user_id=by_user_id, at=now)
            await tx.flush()
            return group
