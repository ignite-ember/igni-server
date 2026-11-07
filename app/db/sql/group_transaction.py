"""One transaction on one organisation's group aggregate.

A group owns rows in several tables, each with its own class
(`GroupMembersDB`, `GroupEntriesDB`, `GroupExtensionsDB`). A change that
reaches more than one of them has to happen in one transaction, so those
classes take the caller's session for that part of their work.
`GroupTransaction` is the one place that hands it to them: the classes
that decide what a change means (`GroupsDB`, `GroupPersonErasureDB`) say
it in terms of the transaction, never of a raw session.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql.group_entries import GroupEntriesDB
from app.db.sql.group_extensions import GroupExtensionsDB
from app.db.sql.group_members import GroupMembersDB
from app.db.sql.models.org_group import OrgGroup
from app.db.sql.schemas.group import GroupNameTaken
from app.db.sql.tenancy import TenantScoped, TenantSessions


class GroupTransaction:
    """The groups of one organisation, and what they own, inside one open transaction."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        org_id: str,
        members: GroupMembersDB,
        entries: GroupEntriesDB,
        extensions: GroupExtensionsDB,
    ) -> None:
        self._session = session
        self._org_id = org_id
        self._members = members
        self._entries = entries
        self._extensions = extensions

    # ── The groups themselves ───────────────────────────────────────

    async def count(self) -> int:
        result = await self._session.execute(select(func.count(OrgGroup.id)).where(OrgGroup.org_id == self._org_id))
        return result.scalar() or 0

    async def page(self, *, offset: int, limit: int | None) -> list[OrgGroup]:
        """Oldest first. An `offset` of 0 and a `limit` of 0 or None are not applied."""
        query = select(OrgGroup).where(OrgGroup.org_id == self._org_id).order_by(OrgGroup.created_at)
        if offset:
            query = query.offset(offset)
        if limit:
            query = query.limit(limit)
        return list((await self._session.execute(query)).scalars().all())

    async def group(self, *, group_id: str) -> OrgGroup | None:
        return await self._first(OrgGroup.id == group_id)

    async def group_named(self, *, name: str) -> OrgGroup | None:
        return await self._first(OrgGroup.name == name)

    async def add(self, group: OrgGroup) -> None:
        """Write the new group, answering a duplicate name as `GroupNameTaken` and anything else as itself."""
        self._session.add(group)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            if not GroupNameTaken.raised_by(exc):
                raise
            raise GroupNameTaken(group.name) from exc

    async def remove(self, group: OrgGroup) -> None:
        await self._session.delete(group)
        await self._session.flush()

    async def flush(self) -> None:
        await self._session.flush()

    # ── What the groups own ─────────────────────────────────────────

    async def return_everyone_in(self, *, group_id: str) -> None:
        """Move everybody in this group to the Default one."""
        await self._members.return_everyone_in(self._session, org_id=self._org_id, from_group_id=group_id)

    async def copy_contents(
        self, *, from_group_id: str, to_group_id: str, by_user_id: str | None, at: datetime
    ) -> None:
        """Copy one group's entries, then its extensions, into another."""
        await self._entries.copy_into(
            self._session, from_group_id=from_group_id, to_group_id=to_group_id, by_user_id=by_user_id, at=at
        )
        await self._extensions.copy_into(
            self._session, from_group_id=from_group_id, to_group_id=to_group_id, by_user_id=by_user_id, at=at
        )

    # ── A person who is gone ────────────────────────────────────────

    async def forget_member(self, *, user_id: str) -> None:
        """Their membership goes; where they placed somebody, their name is cleared."""
        await self._members.forget(self._session, user_id=user_id)

    async def forget_creator(self, *, user_id: str) -> None:
        """Groups they created stay; their name on them is cleared."""
        await self._session.execute(
            update(OrgGroup).where(OrgGroup.created_by_user_id == user_id).values(created_by_user_id=None)
        )

    async def forget_contributor(self, *, user_id: str) -> None:
        """Entries they created and extensions they switched on stay; their name on them is cleared."""
        await self._entries.forget_creator(self._session, user_id=user_id)
        await self._extensions.forget_enabler(self._session, user_id=user_id)

    async def _first(self, *where: ColumnElement[bool]) -> OrgGroup | None:
        result = await self._session.execute(select(OrgGroup).where(OrgGroup.org_id == self._org_id, *where))
        return result.scalars().first()


class GroupAggregate(TenantScoped):
    """Base for the classes that change an organisation's groups through a `GroupTransaction`.

    Not a singleton: it holds the collaborators it was given, and a
    second construction with different ones must get them.
    """

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

    @asynccontextmanager
    async def _transaction(self, org_id: str) -> AsyncIterator[GroupTransaction]:
        """A transaction on the organisation's database: committed on success, rolled back on error."""
        async with self.session_scope(org_id) as session:
            yield GroupTransaction(
                session, org_id=org_id, members=self._members, entries=self._entries, extensions=self._extensions
            )
