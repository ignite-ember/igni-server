"""An organisation's groups, as one aggregate with their members, entries and extensions.

Groups live in the organisation's own database, so every method names the
organisation. The rows a group owns belong to their own classes
(`GroupMembersDB`, `GroupEntriesDB`, `GroupExtensionsDB`); this one opens
the transaction when a change has to reach several of them at once, and
hands each its share of the work inside it.
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql.group_entries import GroupEntriesDB
from app.db.sql.group_extensions import GroupExtensionsDB
from app.db.sql.group_members import GroupMembersDB
from app.db.sql.models.org_group import OrgGroup
from app.db.sql.schemas.group import (
    GroupNameTaken,
    GroupNotFound,
    GroupPage,
    GroupPatch,
    GroupSettings,
    TemplateGroupNotFound,
)
from app.db.sql.tenancy import TenantData, TenantSessions


class GroupsDB(TenantData):
    """Org groups, scoped to an organisation."""

    def __init__(
        self,
        *,
        sessions: TenantSessions | None = None,
        members: GroupMembersDB | None = None,
        entries: GroupEntriesDB | None = None,
        extensions: GroupExtensionsDB | None = None,
    ) -> None:
        super().__init__(sessions=sessions)
        self._members = members or GroupMembersDB()
        self._entries = entries or GroupEntriesDB()
        self._extensions = extensions or GroupExtensionsDB()

    async def list_groups(self, org_id: str, *, offset: int = 0, limit: int | None = None) -> GroupPage:
        """The organisation's groups, oldest first.

        An `offset` of 0 and a `limit` of 0 or None both mean "not
        applied", so `limit=0` returns every group rather than none.
        """
        async with self.session_scope(org_id) as session:
            total = (
                await session.execute(select(func.count(OrgGroup.id)).where(OrgGroup.org_id == org_id))
            ).scalar() or 0
            query = select(OrgGroup).where(OrgGroup.org_id == org_id).order_by(OrgGroup.created_at)
            if offset:
                query = query.offset(offset)
            if limit:
                query = query.limit(limit)
            rows = (await session.execute(query)).scalars().all()
            return GroupPage(items=list(rows), total=total)

    async def get_group(self, org_id: str, group_id: str) -> OrgGroup | None:
        async with self.session_scope(org_id) as session:
            return await self._in_org(session, org_id=org_id, group_id=group_id)

    async def get_group_by_name(self, org_id: str, name: str) -> OrgGroup | None:
        async with self.session_scope(org_id) as session:
            result = await session.execute(select(OrgGroup).where(OrgGroup.org_id == org_id, OrgGroup.name == name))
            return result.scalars().first()

    async def create_group(
        self, org_id: str, *, settings: GroupSettings, by_user_id: str | None, is_default: bool = False
    ) -> OrgGroup:
        """A new, empty group. `GroupNameTaken` when the organisation has one by that name."""
        async with self.session_scope(org_id) as session:
            group = self._new_group(
                org_id,
                settings=settings,
                by_user_id=by_user_id,
                is_default=is_default,
                vm_profile_id=None,
                at=datetime.now(timezone.utc),
            )
            await self._add(session, group=group, name=settings.name)
            return group

    async def update_group(self, org_id: str, group_id: str, *, patch: GroupPatch) -> OrgGroup:
        """Apply the fields the patch supplies. `GroupNotFound` when there is no such group."""
        async with self.session_scope(org_id) as session:
            row = await self._in_org(session, org_id=org_id, group_id=group_id)
            if row is None:
                raise GroupNotFound()
            if patch.name is not None:
                row.name = patch.name
            if patch.description is not None:
                row.description = patch.description
            if 'default_model' in patch.model_fields_set:
                row.default_model = patch.default_model
            if 'theme' in patch.model_fields_set:
                row.theme = patch.theme
            row.updated_at = datetime.now(timezone.utc)
            await session.flush()
            return row

    async def delete_group(self, org_id: str, group_id: str) -> None:
        """Delete a group; its people go back to the Default group in the same transaction.

        The membership rows cascade with the group, so without the move
        everybody in it would be in no group at all until something
        happened to ask — and a person is always in exactly one.
        `GroupNotFound` when there is no such group, `NoDefaultGroup`
        when there is nowhere to move them.
        """
        async with self.session_scope(org_id) as session:
            row = await self._in_org(session, org_id=org_id, group_id=group_id)
            if row is None:
                raise GroupNotFound()
            await self._members.return_everyone_in(session, org_id=org_id, from_group_id=group_id)
            await session.delete(row)
            await session.flush()

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
        async with self.session_scope(org_id) as session:
            source = await self._in_org(session, org_id=org_id, group_id=from_group_id)
            if source is None:
                raise TemplateGroupNotFound()
            now = datetime.now(timezone.utc)
            group = self._new_group(
                org_id,
                settings=settings,
                by_user_id=by_user_id,
                is_default=False,
                vm_profile_id=source.vm_profile_id,
                at=now,
            )
            await self._add(session, group=group, name=settings.name)
            await self._entries.copy_into(
                session, from_group_id=from_group_id, to_group_id=group.id, by_user_id=by_user_id, at=now
            )
            await self._extensions.copy_into(
                session, from_group_id=from_group_id, to_group_id=group.id, by_user_id=by_user_id, at=now
            )
            await session.flush()
            return group

    async def forget_person(self, org_id: str, user_id: str) -> None:
        """A person's account is gone: what the foreign keys to `users` used to do.

        Groups are in the organisation's own database and `users` is in the
        shared plane, so nothing cascades between them any more. Their
        group membership goes; where they appear as the one who created
        or added something, the record stays and the name is cleared.
        """
        async with self.session_scope(org_id) as session:
            await self._members.forget(session, user_id=user_id)
            await self._forget_creator(session, user_id=user_id)
            await self._entries.forget_creator(session, user_id=user_id)
            await self._extensions.forget_enabler(session, user_id=user_id)

    @staticmethod
    def _new_group(
        org_id: str,
        *,
        settings: GroupSettings,
        by_user_id: str | None,
        is_default: bool,
        vm_profile_id: str | None,
        at: datetime,
    ) -> OrgGroup:
        return OrgGroup(
            id=str(uuid.uuid4()),
            org_id=org_id,
            name=settings.name,
            description=settings.description,
            default_model=settings.default_model,
            theme=settings.theme,
            vm_profile_id=vm_profile_id,
            is_default=is_default,
            created_by_user_id=by_user_id,
            created_at=at,
            updated_at=at,
        )

    async def _add(self, session: AsyncSession, *, group: OrgGroup, name: str) -> None:
        """Write the new group, answering a duplicate name as `GroupNameTaken` and anything else as itself."""
        session.add(group)
        try:
            await session.flush()
        except IntegrityError as exc:
            if not GroupNameTaken.raised_by(exc):
                raise
            raise GroupNameTaken(name) from exc

    async def _in_org(self, session: AsyncSession, *, org_id: str, group_id: str) -> OrgGroup | None:
        result = await session.execute(select(OrgGroup).where(OrgGroup.org_id == org_id, OrgGroup.id == group_id))
        return result.scalars().first()

    async def _forget_creator(self, session: AsyncSession, *, user_id: str) -> None:
        await session.execute(
            update(OrgGroup).where(OrgGroup.created_by_user_id == user_id).values(created_by_user_id=None)
        )
