"""What each group of an organisation holds: its agents, skills, hooks and the rest."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql.models.org_group_entry import OrgGroupEntry
from app.db.sql.schemas.group import EntryCreate, EntryNameTaken, EntryNotFound, EntryPatch
from app.db.sql.tenancy import TenantData


class GroupEntriesDB(TenantData):
    """Group entries, scoped to an organisation."""

    async def list_entries(self, org_id: str, group_id: str) -> list[OrgGroupEntry]:
        async with self.session_scope(org_id) as session:
            rows = await session.execute(
                select(OrgGroupEntry)
                .where(OrgGroupEntry.group_id == group_id)
                .order_by(OrgGroupEntry.kind, OrgGroupEntry.entry_name)
            )
            return list(rows.scalars().all())

    async def count_by_group(self, session: AsyncSession, *, group_ids: list[str]) -> dict[str, int]:
        """How many entries each of these groups holds, in the caller's transaction. A group with none is absent."""
        rows = await session.execute(
            select(OrgGroupEntry.group_id, func.count(OrgGroupEntry.id))
            .where(OrgGroupEntry.group_id.in_(group_ids))
            .group_by(OrgGroupEntry.group_id)
        )
        return {str(group_id): int(count) for group_id, count in rows.all()}

    async def get_entry(self, org_id: str, *, group_id: str, entry_id: str) -> OrgGroupEntry | None:
        async with self.session_scope(org_id) as session:
            return await self._in_group(session, group_id=group_id, entry_id=entry_id)

    async def create_entry(
        self, org_id: str, *, group_id: str, entry: EntryCreate, by_user_id: str | None
    ) -> OrgGroupEntry:
        """A new entry. `EntryNameTaken` when the group has one of that kind and name already.

        Any other integrity failure (a group deleted meanwhile, say) is
        not "already exists", and is re-raised as itself.
        """
        async with self.session_scope(org_id) as session:
            now = datetime.now(timezone.utc)
            row = OrgGroupEntry(
                id=str(uuid.uuid4()),
                group_id=group_id,
                kind=entry.kind,
                entry_name=entry.entry_name,
                content=entry.content,
                content_type=entry.content_type,
                enabled=entry.enabled,
                source_url=entry.source_url,
                source_ref=entry.source_ref,
                source_subdir=entry.source_subdir,
                model=entry.model,
                created_by_user_id=by_user_id,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            try:
                await session.flush()
            except IntegrityError as exc:
                if not EntryNameTaken.raised_by(exc):
                    raise
                raise EntryNameTaken(kind=entry.kind, entry_name=entry.entry_name) from exc
            return row

    async def update_entry(self, org_id: str, *, group_id: str, entry_id: str, patch: EntryPatch) -> OrgGroupEntry:
        """Apply the fields the patch supplies. `EntryNotFound` when there is no such entry."""
        async with self.session_scope(org_id) as session:
            row = await self._in_group(session, group_id=group_id, entry_id=entry_id)
            if row is None:
                raise EntryNotFound()
            if patch.content is not None:
                row.content = patch.content
            if patch.enabled is not None:
                row.enabled = patch.enabled
            if 'model' in patch.model_fields_set:
                row.model = patch.model
            row.updated_at = datetime.now(timezone.utc)
            await session.flush()
            return row

    async def delete_entry(self, org_id: str, *, group_id: str, entry_id: str) -> None:
        """`EntryNotFound` when there is no such entry."""
        async with self.session_scope(org_id) as session:
            row = await self._in_group(session, group_id=group_id, entry_id=entry_id)
            if row is None:
                raise EntryNotFound()
            await session.delete(row)
            await session.flush()

    # ------------------------------------------------------------------
    # Inside a transaction the caller opened
    # ------------------------------------------------------------------

    async def copy_into(
        self, session: AsyncSession, *, from_group_id: str, to_group_id: str, by_user_id: str | None, at: datetime
    ) -> None:
        """Copy every entry of one group into another, in the caller's transaction."""
        rows = await session.execute(select(OrgGroupEntry).where(OrgGroupEntry.group_id == from_group_id))
        session.add_all(
            OrgGroupEntry(
                id=str(uuid.uuid4()),
                group_id=to_group_id,
                kind=entry.kind,
                entry_name=entry.entry_name,
                content=entry.content,
                content_type=entry.content_type,
                enabled=entry.enabled,
                source_url=entry.source_url,
                source_ref=entry.source_ref,
                source_subdir=entry.source_subdir,
                model=entry.model,
                created_by_user_id=by_user_id,
                created_at=at,
                updated_at=at,
            )
            for entry in rows.scalars().all()
        )

    async def forget_creator(self, session: AsyncSession, *, user_id: str) -> None:
        """Entries they created stay; their name on them is cleared."""
        await session.execute(
            update(OrgGroupEntry).where(OrgGroupEntry.created_by_user_id == user_id).values(created_by_user_id=None)
        )

    async def _in_group(self, session: AsyncSession, *, group_id: str, entry_id: str) -> OrgGroupEntry | None:
        rows = await session.execute(
            select(OrgGroupEntry).where(OrgGroupEntry.group_id == group_id, OrgGroupEntry.id == entry_id)
        )
        return rows.scalars().first()
