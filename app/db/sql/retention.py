"""The retention of removed members' VMs, and legal holds, on the shared plane."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.retention import LegalHold, OrgRetention, PlatformRetention
from app.db.sql.producer_feed import chunks
from app.utils.singleton import SingletonMeta


class RetentionDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    async def platform(self) -> PlatformRetention | None:
        async with self.session_scope() as session:
            return await session.get(PlatformRetention, 1)

    async def put_platform(self, row: PlatformRetention) -> None:
        async with self.session_scope() as session:
            row.id = 1
            row.updated_at = datetime.now(timezone.utc)
            await session.merge(row)

    async def org(self, org_id: str) -> OrgRetention | None:
        async with self.session_scope() as session:
            return await session.get(OrgRetention, org_id)

    async def put_org(self, row: OrgRetention) -> None:
        async with self.session_scope() as session:
            row.updated_at = datetime.now(timezone.utc)
            await session.merge(row)

    async def holds(self, org_id: str) -> list[LegalHold]:
        async with self.session_scope() as session:
            rows = await session.execute(
                select(LegalHold).where(LegalHold.org_id == org_id).order_by(LegalHold.placed_at)
            )
            return list(rows.scalars().all())

    async def hold(self, org_id: str, user_id: str) -> LegalHold | None:
        async with self.session_scope() as session:
            return await session.get(LegalHold, (org_id, user_id))

    async def holding(self, user_id: str) -> list[str]:
        """Every organisation with a legal hold on this person."""
        async with self.session_scope() as session:
            rows = await session.execute(select(LegalHold.org_id).where(LegalHold.user_id == user_id))
            return [str(org_id) for org_id in rows.scalars().all()]

    async def held_in(self, org_id: str, user_ids: list[str]) -> set[str]:
        found: set[str] = set()
        async with self.session_scope() as session:
            for part in chunks(user_ids):
                rows = await session.execute(
                    select(LegalHold.user_id).where(LegalHold.org_id == org_id, LegalHold.user_id.in_(part))
                )
                found.update(str(user_id) for user_id in rows.scalars().all())
        return found

    async def put_hold(self, hold: LegalHold) -> None:
        values = {
            'org_id': hold.org_id,
            'user_id': hold.user_id,
            'reason': hold.reason,
            'placed_by_user_id': hold.placed_by_user_id,
            'placed_at': datetime.now(timezone.utc),
        }
        async with self.session_scope() as session:
            await session.execute(
                insert(LegalHold).values(**values).on_conflict_do_update(constraint='pk_legal_holds', set_=values)
            )

    async def release(self, org_id: str, user_id: str) -> bool:
        async with self.session_scope() as session:
            result = await session.execute(
                delete(LegalHold).where(LegalHold.org_id == org_id, LegalHold.user_id == user_id)
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]
