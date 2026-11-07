"""Personal API keys, in the organisation's own database (`docs/service-identity.md` §22)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update

from app.db.sql.models.api_key import ApiKey
from app.db.sql.tenancy import TenantData


class ApiKeysDB(TenantData):
    async def add(self, org_id: str, key: ApiKey) -> ApiKey:
        async with self.session_scope(org_id) as session:
            session.add(key)
        return key

    async def get(self, org_id: str, key_id: str) -> ApiKey | None:
        async with self.session_scope(org_id) as session:
            return await session.get(ApiKey, key_id)

    async def working(self, org_id: str, *, user_id: str | None = None) -> list[ApiKey]:
        """Keys not revoked — expired ones included, so the portal can say so."""
        query = select(ApiKey).where(ApiKey.revoked_at.is_(None))
        if user_id:
            query = query.where(ApiKey.user_id == user_id)
        async with self.session_scope(org_id) as session:
            rows = await session.execute(query.order_by(ApiKey.created_at))
            return list(rows.scalars().all())

    async def used(self, org_id: str, key_id: str) -> None:
        async with self.session_scope(org_id) as session:
            await session.execute(
                update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=datetime.now(timezone.utc))
            )

    async def revoke(self, org_id: str, key_id: str, *, reason: str, by_id: str | None) -> bool:
        async with self.session_scope(org_id) as session:
            result = await session.execute(
                update(ApiKey)
                .where(ApiKey.id == key_id, ApiKey.revoked_at.is_(None))
                .values(revoked_at=datetime.now(timezone.utc), revoked_reason=reason, revoked_by_id=by_id)
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def revoke_person(self, org_id: str, user_id: str, *, reason: str) -> int:
        async with self.session_scope(org_id) as session:
            result = await session.execute(
                update(ApiKey)
                .where(ApiKey.user_id == user_id, ApiKey.revoked_at.is_(None))
                .values(revoked_at=datetime.now(timezone.utc), revoked_reason=reason)
            )
            return int(result.rowcount or 0)  # type: ignore[attr-defined]
