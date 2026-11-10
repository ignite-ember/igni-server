"""An app's spaces, in the organisation's own database; the limits on them, on the shared plane."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, func, select, text

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.bind_chunks import chunks
from app.db.sql.models.app_space import AppSpace
from app.db.sql.models.space_limits import OrgSpaceQuota, PlatformSpaceCaps
from app.db.sql.tenancy import TenantData
from app.utils.singleton import SingletonMeta

#: Serialises declaring spaces within one organisation's database, so two
#: at once cannot both pass the count.
_SPACES_LOCK = 0x5350414345


class SpaceLimitReached(Exception):
    """Declaring one more would pass a limit; the message says which."""


class SpacesDB(TenantData):
    async def get(self, org_id: str, space_id: str) -> AppSpace | None:
        async with self.session_scope(org_id) as session:
            return await session.get(AppSpace, space_id)

    async def by_external(self, org_id: str, app_id: str, external_id: str) -> AppSpace | None:
        async with self.session_scope(org_id) as session:
            rows = await session.execute(
                select(AppSpace).where(AppSpace.app_id == app_id, AppSpace.external_id == external_id)
            )
            return rows.scalars().first()

    async def declare(
        self, org_id: str, space: AppSpace, *, per_app: int | None, per_org: int | None
    ) -> tuple[AppSpace, bool]:
        """The app's space by this external id: the one there, or *space*, new. True when new.

        Counted and inserted under a lock on this organisation's database,
        so the limits hold when declarations race.
        """
        async with self.session_scope(org_id) as session:
            await session.execute(text('SELECT pg_advisory_xact_lock(:k)'), {'k': _SPACES_LOCK})
            found = (
                (
                    await session.execute(
                        select(AppSpace).where(
                            AppSpace.app_id == space.app_id, AppSpace.external_id == space.external_id
                        )
                    )
                )
                .scalars()
                .first()
            )
            if found is not None:
                return found, False
            if per_app is not None:
                count = await session.scalar(
                    select(func.count()).select_from(AppSpace).where(AppSpace.app_id == space.app_id)
                )
                if int(count or 0) >= per_app:
                    raise SpaceLimitReached(f'This app may have at most {per_app} spaces')
            if per_org is not None:
                count = await session.scalar(select(func.count()).select_from(AppSpace))
                if int(count or 0) >= per_org:
                    raise SpaceLimitReached(f'This organisation may have at most {per_org} spaces')
            session.add(space)
            await session.flush()
            return space, True

    async def page(self, org_id: str, app_id: str, *, after: str | None, limit: int) -> list[AppSpace]:
        query = select(AppSpace).where(AppSpace.app_id == app_id)
        if after is not None:
            query = query.where(AppSpace.external_id > after)
        async with self.session_scope(org_id) as session:
            rows = await session.execute(query.order_by(AppSpace.external_id).limit(limit))
            return list(rows.scalars().all())

    async def remove(self, org_id: str, space_ids: list[str]) -> None:
        async with self.session_scope(org_id) as session:
            for part in chunks(space_ids):
                await session.execute(delete(AppSpace).where(AppSpace.id.in_(part)))

    async def count(self, org_id: str) -> int:
        async with self.session_scope(org_id) as session:
            return int(await session.scalar(select(func.count()).select_from(AppSpace)) or 0)

    async def apps_of(self, org_id: str, space_ids: list[str] | None) -> dict[str, str]:
        """Each of these spaces (every one, for None) that exists, with its app."""
        found: dict[str, str] = {}
        async with self.session_scope(org_id) as session:
            if space_ids is None:
                rows = await session.execute(select(AppSpace.id, AppSpace.app_id))
                return {str(space_id): str(app_id) for space_id, app_id in rows.all()}
            for part in chunks(space_ids):
                rows = await session.execute(select(AppSpace.id, AppSpace.app_id).where(AppSpace.id.in_(part)))
                found.update({str(space_id): str(app_id) for space_id, app_id in rows.all()})
        return found

    async def of_app(self, org_id: str, app_id: str) -> list[str]:
        """Every space of one app."""
        async with self.session_scope(org_id) as session:
            rows = await session.execute(select(AppSpace.id).where(AppSpace.app_id == app_id))
            return [str(space_id) for space_id in rows.scalars().all()]


class SpaceLimitsDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    async def caps(self) -> PlatformSpaceCaps | None:
        async with self.session_scope() as session:
            return await session.get(PlatformSpaceCaps, 1)

    async def put_caps(self, caps: PlatformSpaceCaps) -> None:
        async with self.session_scope() as session:
            caps.id = 1
            caps.updated_at = datetime.now(timezone.utc)
            await session.merge(caps)

    async def quota(self, org_id: str) -> OrgSpaceQuota | None:
        async with self.session_scope() as session:
            return await session.get(OrgSpaceQuota, org_id)

    async def put_quota(self, quota: OrgSpaceQuota) -> None:
        async with self.session_scope() as session:
            quota.updated_at = datetime.now(timezone.utc)
            await session.merge(quota)
