"""Database layer for org group management (org-scoped).

Groups, their members and their entries live in the organisation's own
database, so every method names the organisation. The one question that
starts from a person alone — which group are they in — first asks the
shared plane which organisation they belong to.
"""

from datetime import datetime, timezone
import logging
import uuid

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql.models.org_group import OrgGroup
from app.db.sql.models.org_group_entry import OrgGroupEntry
from app.db.sql.models.org_group_extension import OrgGroupExtension
from app.db.sql.models.org_group_member import OrgGroupMember
from app.db.sql.organizations import OrganizationDB
from app.db.sql.schemas.group import GroupMembership, GroupSummary
from app.db.sql.tenancy import TenantData, TenantSessions

logger = logging.getLogger(__name__)

#: The constraint a duplicate group name violates.
_GROUP_NAME_INDEX = 'uq_org_groups_org_name'

#: The constraint that keeps a person in one group.
ONE_GROUP_PER_PERSON = 'uq_org_group_members_user_one_group'

_DEFAULT_KEEPS_ITS_PEOPLE = (
    'Everybody is in exactly one group, and the Default group is where '
    'people go when they leave any other — so nobody leaves it except by '
    'being moved into another group: POST /portal/groups/{group_id}/members.'
)


class GroupsDB(TenantData):
    """Database operations for org groups, scoped to an org."""

    def __init__(
        self,
        *,
        sessions: TenantSessions | None = None,
        organizations: OrganizationDB | None = None,
    ) -> None:
        super().__init__(sessions=sessions)
        self._organizations = organizations or OrganizationDB()

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    async def list_groups(self, org_id: str, offset: int = 0, limit: int | None = None) -> tuple[list[OrgGroup], int]:
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
            return list(rows), total

    async def get_group(self, org_id: str, group_id: str) -> OrgGroup | None:
        async with self.session_scope(org_id) as session:
            result = await session.execute(
                select(OrgGroup).where(
                    OrgGroup.org_id == org_id,
                    OrgGroup.id == group_id,
                )
            )
            return result.scalars().first()

    async def get_group_by_name(self, org_id: str, name: str) -> OrgGroup | None:
        async with self.session_scope(org_id) as session:
            result = await session.execute(
                select(OrgGroup).where(
                    OrgGroup.org_id == org_id,
                    OrgGroup.name == name,
                )
            )
            return result.scalars().first()

    async def create_group(
        self,
        org_id: str,
        name: str,
        description: str | None,
        created_by_user_id: str,
        default_model: str | None = None,
        is_default: bool = False,
        theme: str | None = None,
    ) -> OrgGroup:
        async with self.session_scope(org_id) as session:
            now = datetime.now(timezone.utc)
            group = OrgGroup(
                id=str(uuid.uuid4()),
                org_id=org_id,
                name=name,
                description=description,
                default_model=default_model,
                theme=theme,
                is_default=is_default,
                created_by_user_id=created_by_user_id,
                created_at=now,
                updated_at=now,
            )
            session.add(group)
            try:
                await session.flush()
            except IntegrityError as exc:
                # ``groups_service.create_group`` pre-checks the name and
                # raises this same 409, which is the fast path. It is a
                # check-then-act though, so two concurrent creates both pass
                # the check: 8 at once gave one 201, three 409s from the
                # pre-check, and four 500s that came through here.
                #
                # The constraint is the backstop, and the message matches the
                # pre-check's so a caller cannot tell which one answered.
                # Matched on the constraint name, not on the word "name"
                # appearing in the driver's message. A NOT NULL
                # violation on this very column says `null value in
                # column "name" ...` — checked against the live database
                # — and would have been reported as "a group named X
                # already exists", which is both the wrong status and a
                # sentence that sends somebody looking for a group that
                # is not there.
                if _GROUP_NAME_INDEX not in str(getattr(exc, 'orig', exc)):
                    raise
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"A group named '{name}' already exists in this organization.",
                ) from exc
            return group

    async def update_group(
        self,
        org_id: str,
        group_id: str,
        name: str | None = None,
        description: str | None = None,
        default_model: str | None = None,
        clear_default_model: bool = False,
        theme: str | None = None,
        clear_theme: bool = False,
    ) -> OrgGroup:
        async with self.session_scope(org_id) as session:
            row = (
                (
                    await session.execute(
                        select(OrgGroup).where(
                            OrgGroup.org_id == org_id,
                            OrgGroup.id == group_id,
                        )
                    )
                )
                .scalars()
                .first()
            )

            if not row:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Group not found')

            if name is not None:
                row.name = name  # type: ignore[assignment]
            if description is not None:
                row.description = description  # type: ignore[assignment]
            # ``None`` means "not supplied" for every other field here, so
            # clearing needs its own flag rather than overloading it.
            if clear_default_model:
                row.default_model = None  # type: ignore[assignment]
            elif default_model is not None:
                row.default_model = default_model  # type: ignore[assignment]
            # Same shape for the same reason: a theme that overrides
            # nothing is stored as NULL, and NULL is also "not supplied".
            if clear_theme:
                row.theme = None  # type: ignore[assignment]
            elif theme is not None:
                row.theme = theme  # type: ignore[assignment]
            row.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
            await session.flush()
            return row

    async def delete_group(self, org_id: str, group_id: str) -> None:
        """Delete a group; its people go back to the Default group in the same transaction.

        The membership rows cascade with the group, so without the move
        everybody in it would be in no group at all until something
        happened to ask — and a person is always in exactly one.
        """
        async with self.session_scope(org_id) as session:
            row = (
                (
                    await session.execute(
                        select(OrgGroup).where(
                            OrgGroup.org_id == org_id,
                            OrgGroup.id == group_id,
                        )
                    )
                )
                .scalars()
                .first()
            )

            if not row:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Group not found')

            default = await self._default_in(session, org_id)
            await session.execute(
                update(OrgGroupMember).where(OrgGroupMember.group_id == group_id).values(group_id=default.id)
            )
            await session.delete(row)
            await session.flush()

    async def copy_group(
        self,
        org_id: str,
        from_group_id: str,
        name: str,
        description: str | None,
        default_model: str | None,
        theme: str | None,
        created_by_user_id: str,
    ) -> OrgGroup:
        """A new group holding a copy of another's entries and extensions — and nobody.

        One transaction: a copy is all of it or none of it, never a
        group that exists with half its entries. The settings it is
        made with are the caller's, so "copy that group" and "start a
        new group from that one" are the same operation with different
        names on the front. People are not copied: a person is in one
        group, and the copy starts empty of them.
        """
        async with self.session_scope(org_id) as session:
            source = (
                (await session.execute(select(OrgGroup).where(OrgGroup.org_id == org_id, OrgGroup.id == from_group_id)))
                .scalars()
                .first()
            )
            if source is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail='The group to copy from does not exist in this organization.',
                )

            now = datetime.now(timezone.utc)
            group = OrgGroup(
                id=str(uuid.uuid4()),
                org_id=org_id,
                name=name,
                description=description,
                default_model=default_model,
                theme=theme,
                # The VM profile is a setting like the theme: the copy's
                # people get the same microVM as the source's.
                vm_profile_id=source.vm_profile_id,
                is_default=False,
                created_by_user_id=created_by_user_id,
                created_at=now,
                updated_at=now,
            )
            session.add(group)
            try:
                await session.flush()
            except IntegrityError as exc:
                if _GROUP_NAME_INDEX not in str(getattr(exc, 'orig', exc)):
                    raise
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"A group named '{name}' already exists in this organization.",
                ) from exc

            entries = (
                (await session.execute(select(OrgGroupEntry).where(OrgGroupEntry.group_id == from_group_id)))
                .scalars()
                .all()
            )
            session.add_all(
                OrgGroupEntry(
                    id=str(uuid.uuid4()),
                    group_id=group.id,
                    kind=entry.kind,
                    entry_name=entry.entry_name,
                    content=entry.content,
                    content_type=entry.content_type,
                    enabled=entry.enabled,
                    source_url=entry.source_url,
                    source_ref=entry.source_ref,
                    source_subdir=entry.source_subdir,
                    model=entry.model,
                    created_by_user_id=created_by_user_id,
                    created_at=now,
                    updated_at=now,
                )
                for entry in entries
            )
            extensions = (
                (
                    await session.execute(
                        select(OrgGroupExtension.extension_id).where(OrgGroupExtension.group_id == from_group_id)
                    )
                )
                .scalars()
                .all()
            )
            session.add_all(
                OrgGroupExtension(
                    id=str(uuid.uuid4()),
                    group_id=group.id,
                    extension_id=extension_id,
                    enabled_by_user_id=created_by_user_id,
                    enabled_at=now,
                )
                for extension_id in extensions
            )
            await session.flush()
            return group

    # ------------------------------------------------------------------
    # Members
    #
    # A person is in exactly one group of their organisation:
    # ``uq_org_group_members_user_one_group`` is on ``user_id`` alone, and
    # each organisation has its own database. So there is no "add" that
    # could leave somebody in two groups — putting a person in a group
    # *is* moving them, one statement, and taking them out of a group
    # puts them back in the Default one.
    # ------------------------------------------------------------------

    async def list_members(self, org_id: str, group_id: str) -> list[OrgGroupMember]:
        async with self.session_scope(org_id) as session:
            rows = (
                (
                    await session.execute(
                        select(OrgGroupMember)
                        .where(OrgGroupMember.group_id == group_id)
                        .order_by(OrgGroupMember.added_at)
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)

    async def memberships(self, org_id: str) -> list[GroupMembership]:
        """Every person with a group in this organisation, and which group it is."""
        async with self.session_scope(org_id) as session:
            rows = await session.execute(
                select(OrgGroupMember.user_id, OrgGroup.id, OrgGroup.name, OrgGroup.is_default)
                .join(OrgGroup, OrgGroup.id == OrgGroupMember.group_id)
                .order_by(OrgGroupMember.user_id)
            )
            return [
                GroupMembership(
                    user_id=str(user_id), group_id=str(group_id), group_name=str(name), is_default=bool(flag)
                )
                for user_id, group_id, name, flag in rows.all()
            ]

    async def get_member_by_user(self, org_id: str, user_id: str) -> OrgGroupMember | None:
        """Return the group membership for a user, if any."""
        async with self.session_scope(org_id) as session:
            result = await session.execute(select(OrgGroupMember).where(OrgGroupMember.user_id == user_id))
            return result.scalars().first()

    async def place(self, org_id: str, group_id: str, user_id: str, added_by_user_id: str | None) -> OrgGroupMember:
        """Put a person in this group, out of whichever they were in.

        One ``INSERT … ON CONFLICT (user_id) DO UPDATE``: there is no
        moment at which they are in no group or in two, and two admins
        placing the same person at once leave them in whichever ran
        last rather than a 409 or a 500. Placing somebody where they
        already are changes nothing.
        """
        async with self.session_scope(org_id) as session:
            now = datetime.now(timezone.utc)
            statement = (
                insert(OrgGroupMember)
                .values(
                    id=str(uuid.uuid4()),
                    group_id=group_id,
                    user_id=user_id,
                    added_by_user_id=added_by_user_id,
                    added_at=now,
                )
                .on_conflict_do_update(
                    constraint=ONE_GROUP_PER_PERSON,
                    set_={'group_id': group_id, 'added_by_user_id': added_by_user_id, 'added_at': now},
                    where=OrgGroupMember.group_id != group_id,
                )
            )
            await session.execute(statement)
            member = (
                (await session.execute(select(OrgGroupMember).where(OrgGroupMember.user_id == user_id))).scalars().one()
            )
            return member

    async def place_if_ungrouped(
        self, org_id: str, group_id: str, user_ids: list[str], added_by_user_id: str | None
    ) -> int:
        """Put each of these people in this group unless they are in one already. How many were placed."""
        if not user_ids:
            return 0
        async with self.session_scope(org_id) as session:
            now = datetime.now(timezone.utc)
            result = await session.execute(
                insert(OrgGroupMember)
                .values(
                    [
                        {
                            'id': str(uuid.uuid4()),
                            'group_id': group_id,
                            'user_id': user_id,
                            'added_by_user_id': added_by_user_id,
                            'added_at': now,
                        }
                        for user_id in user_ids
                    ]
                )
                .on_conflict_do_nothing(constraint=ONE_GROUP_PER_PERSON)
                .returning(OrgGroupMember.user_id)
            )
            return len(result.all())

    async def return_to_default(self, org_id: str, group_id: str, user_id: str, by_user_id: str | None) -> None:
        """Take a person out of this group: they go back to the Default one.

        404 when they are not in this group; 409 when this *is* the
        Default group, which nobody leaves except by being placed in
        another — there is nowhere further back to put them.
        """
        async with self.session_scope(org_id) as session:
            row = (
                (
                    await session.execute(
                        select(OrgGroupMember).where(
                            OrgGroupMember.group_id == group_id,
                            OrgGroupMember.user_id == user_id,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if not row:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Member not found')

            default = await self._default_in(session, org_id)
            if str(default.id) == group_id:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_DEFAULT_KEEPS_ITS_PEOPLE)

            row.group_id = default.id
            row.added_by_user_id = by_user_id  # type: ignore[assignment]
            row.added_at = datetime.now(timezone.utc)  # type: ignore[assignment]
            await session.flush()

    @staticmethod
    async def _default_in(session: AsyncSession, org_id: str) -> OrgGroup:
        """The organisation's Default group, read in this transaction.

        Callers make sure it exists first (``DefaultGroup.ensure_for_org``);
        one missing here is a bug, not a state to recover from.
        """
        found = (
            (await session.execute(select(OrgGroup).where(OrgGroup.org_id == org_id, OrgGroup.is_default.is_(True))))
            .scalars()
            .first()
        )
        if found is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail='This organisation has no Default group yet.'
            )
        return found

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
        member of it.
        """
        active = await self._organizations.member_org_ids(user_id, active_only=True)
        if org_id is not None:
            # Only in this organisation: a person in two answers for the one
            # they are signed into, never for whichever comes first.
            active = [org for org in active if str(org) == str(org_id)]
        return await self._summary(user_id, active)

    async def get_user_group_summary(self, user_id: str) -> GroupSummary | None:
        """Return {id, org_id, name} for the group a user belongs to, or None.

        Says nothing about whether the organisation membership underneath
        is active — see :meth:`get_active_user_group_summary`, which is
        what the self-serve paths use.
        """
        return await self._summary(user_id, await self._organizations.member_org_ids(user_id, active_only=False))

    async def _summary(self, user_id: str, org_ids: list[str]) -> GroupSummary | None:
        """The person's group in the first of these organisations that has one."""
        for org_id in org_ids:
            async with self.session_scope(org_id) as session:
                group = (
                    (
                        await session.execute(
                            select(OrgGroup)
                            .join(OrgGroupMember, OrgGroupMember.group_id == OrgGroup.id)
                            .where(OrgGroupMember.user_id == user_id, OrgGroup.org_id == org_id)
                        )
                    )
                    .scalars()
                    .first()
                )
            if group is not None:
                return GroupSummary(id=str(group.id), org_id=str(group.org_id), name=str(group.name))
        return None

    async def forget_person(self, org_id: str, user_id: str) -> None:
        """A person's account is gone: what the foreign keys to `users` used to do.

        Groups are in the organisation's own database and `users` is in the
        shared plane, so nothing cascades between them any more. Their
        group membership goes; where they appear as the one who created
        or added something, the record stays and the name is cleared.
        """
        async with self.session_scope(org_id) as session:
            await session.execute(delete(OrgGroupMember).where(OrgGroupMember.user_id == user_id))
            await session.execute(
                update(OrgGroupMember).where(OrgGroupMember.added_by_user_id == user_id).values(added_by_user_id=None)
            )
            await session.execute(
                update(OrgGroup).where(OrgGroup.created_by_user_id == user_id).values(created_by_user_id=None)
            )
            await session.execute(
                update(OrgGroupEntry).where(OrgGroupEntry.created_by_user_id == user_id).values(created_by_user_id=None)
            )
            await session.execute(
                update(OrgGroupExtension)
                .where(OrgGroupExtension.enabled_by_user_id == user_id)
                .values(enabled_by_user_id=None)
            )

    # ------------------------------------------------------------------
    # Entrys
    # ------------------------------------------------------------------

    async def list_entries(self, org_id: str, group_id: str) -> list[OrgGroupEntry]:
        async with self.session_scope(org_id) as session:
            rows = (
                (
                    await session.execute(
                        select(OrgGroupEntry)
                        .where(OrgGroupEntry.group_id == group_id)
                        .order_by(OrgGroupEntry.kind, OrgGroupEntry.entry_name)
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)

    async def get_entry(
        self,
        org_id: str,
        group_id: str,
        entry_id: str,
    ) -> OrgGroupEntry | None:
        async with self.session_scope(org_id) as session:
            result = await session.execute(
                select(OrgGroupEntry).where(
                    OrgGroupEntry.group_id == group_id,
                    OrgGroupEntry.id == entry_id,
                )
            )
            return result.scalars().first()

    async def get_entry_by_name(
        self,
        org_id: str,
        group_id: str,
        kind: str,
        entry_name: str,
    ) -> OrgGroupEntry | None:
        async with self.session_scope(org_id) as session:
            result = await session.execute(
                select(OrgGroupEntry).where(
                    OrgGroupEntry.group_id == group_id,
                    OrgGroupEntry.kind == kind,
                    OrgGroupEntry.entry_name == entry_name,
                )
            )
            return result.scalars().first()

    async def create_entry(
        self,
        org_id: str,
        group_id: str,
        kind: str,
        entry_name: str,
        content: str,
        content_type: str,
        enabled: bool,
        created_by_user_id: str,
        source_url: str | None = None,
        source_ref: str | None = None,
        source_subdir: str | None = None,
        model: str | None = None,
    ) -> OrgGroupEntry:
        async with self.session_scope(org_id) as session:
            now = datetime.now(timezone.utc)
            entry = OrgGroupEntry(
                id=str(uuid.uuid4()),
                group_id=group_id,
                kind=kind,
                entry_name=entry_name,
                content=content,
                content_type=content_type,
                enabled=enabled,
                source_url=source_url,
                source_ref=source_ref,
                source_subdir=source_subdir,
                model=model,
                created_by_user_id=created_by_user_id,
                created_at=now,
                updated_at=now,
            )
            session.add(entry)
            try:
                await session.flush()
            except IntegrityError:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"An entry named '{entry_name}' of kind '{kind}' already exists in this group.",
                )
            return entry

    async def update_entry(
        self,
        org_id: str,
        group_id: str,
        entry_id: str,
        content: str | None = None,
        enabled: bool | None = None,
        model: str | None = None,
        clear_model: bool = False,
    ) -> OrgGroupEntry:
        async with self.session_scope(org_id) as session:
            row = (
                (
                    await session.execute(
                        select(OrgGroupEntry).where(
                            OrgGroupEntry.group_id == group_id,
                            OrgGroupEntry.id == entry_id,
                        )
                    )
                )
                .scalars()
                .first()
            )

            if not row:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Entry not found')

            if content is not None:
                row.content = content  # type: ignore[assignment]
            if enabled is not None:
                row.enabled = enabled  # type: ignore[assignment]
            # ``None`` means "not supplied" for everything else here, so
            # clearing the model needs its own flag.
            if clear_model:
                row.model = None  # type: ignore[assignment]
            elif model is not None:
                row.model = model  # type: ignore[assignment]
            row.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
            await session.flush()
            return row

    async def delete_entry(self, org_id: str, group_id: str, entry_id: str) -> None:
        async with self.session_scope(org_id) as session:
            row = (
                (
                    await session.execute(
                        select(OrgGroupEntry).where(
                            OrgGroupEntry.group_id == group_id,
                            OrgGroupEntry.id == entry_id,
                        )
                    )
                )
                .scalars()
                .first()
            )

            if not row:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Entry not found')

            await session.delete(row)
            await session.flush()
