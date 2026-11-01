"""VM profiles, who has which, and the platform's caps and organisations' quotas.

Profiles and their assignment are an organisation's own data, in its
database. The caps and quotas are the deployment's, on the shared plane.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.org_app import OrgApp
from app.db.sql.models.org_group import OrgGroup
from app.db.sql.models.org_vm_quota import OrgVmQuota
from app.db.sql.models.platform_vm_caps import PlatformVmCaps
from app.db.sql.models.vm_profile import VmProfile
from app.db.sql.tenancy import TenantData
from app.utils.singleton import SingletonMeta


class VmProfileNameTaken(ValueError):
    """Another profile of the organisation has this name."""


class VmProfilesDB(TenantData):
    """One organisation's profiles and their assignment to groups and apps."""

    async def default(self, org_id: str, standard: VmProfile) -> VmProfile:
        """The organisation's default profile, made from *standard* the first time it is asked for."""
        async with self.session_scope(org_id) as session:
            found = (await session.execute(select(VmProfile).where(VmProfile.is_default))).scalars().first()
            if found is not None:
                return found
            await session.execute(
                insert(VmProfile)
                .values(
                    id=standard.id,
                    name=standard.name,
                    is_default=True,
                    memory_mib=standard.memory_mib,
                    normal_memory_mib=standard.normal_memory_mib,
                    vcpus=standard.vcpus,
                    idle_before_pause_s=standard.idle_before_pause_s,
                    expected_memory_mib=standard.expected_memory_mib,
                    packages=standard.packages,
                    setup_script=standard.setup_script,
                    volume_gib=standard.volume_gib,
                    volume_max_gib=standard.volume_max_gib,
                    erase_after_idle_days=standard.erase_after_idle_days,
                )
                .on_conflict_do_nothing()
            )
        async with self.session_scope(org_id) as session:
            made = (await session.execute(select(VmProfile).where(VmProfile.is_default))).scalars().first()
            assert made is not None
            return made

    async def all(self, org_id: str) -> list[VmProfile]:
        async with self.session_scope(org_id) as session:
            rows = await session.execute(select(VmProfile).order_by(VmProfile.is_default.desc(), VmProfile.name))
            return list(rows.scalars().all())

    async def get(self, org_id: str, profile_id: str) -> VmProfile | None:
        async with self.session_scope(org_id) as session:
            return await session.get(VmProfile, profile_id)

    async def add(self, org_id: str, profile: VmProfile) -> VmProfile:
        try:
            async with self.session_scope(org_id) as session:
                session.add(profile)
        except IntegrityError as exc:
            raise VmProfileNameTaken(profile.name) from exc
        return profile

    async def replace(self, org_id: str, profile_id: str, values: dict[str, object], by_user_id: str) -> VmProfile:
        try:
            async with self.session_scope(org_id) as session:
                await session.execute(
                    update(VmProfile)
                    .where(VmProfile.id == profile_id)
                    .values(**values, updated_by_user_id=by_user_id, updated_at=datetime.now(timezone.utc))
                )
        except IntegrityError as exc:
            raise VmProfileNameTaken(str(values.get('name'))) from exc
        found = await self.get(org_id, profile_id)
        assert found is not None
        return found

    async def remove(self, org_id: str, profile_id: str) -> None:
        """Delete it; the groups and apps that had it go back to the default (`SET NULL`)."""
        async with self.session_scope(org_id) as session:
            profile = await session.get(VmProfile, profile_id)
            if profile is not None:
                await session.delete(profile)

    async def groups(self, org_id: str) -> list[OrgGroup]:
        async with self.session_scope(org_id) as session:
            rows = await session.execute(select(OrgGroup).order_by(OrgGroup.is_default.desc(), OrgGroup.name))
            return list(rows.scalars().all())

    async def apps(self, org_id: str) -> list[OrgApp]:
        async with self.session_scope(org_id) as session:
            rows = await session.execute(select(OrgApp).order_by(OrgApp.created_at))
            return list(rows.scalars().all())

    async def assign_group(self, org_id: str, group_id: str, profile_id: str | None) -> bool:
        async with self.session_scope(org_id) as session:
            group = await session.get(OrgGroup, group_id)
            if group is None:
                return False
            group.vm_profile_id = profile_id
            return True

    async def assign_app(self, org_id: str, app_id: str, profile_id: str | None) -> bool:
        async with self.session_scope(org_id) as session:
            app = await session.get(OrgApp, app_id)
            if app is None:
                return False
            app.vm_profile_id = profile_id
            return True

    async def of_group(self, org_id: str, group_id: str) -> str | None:
        async with self.session_scope(org_id) as session:
            group = await session.get(OrgGroup, group_id)
            return group.vm_profile_id if group is not None else None

    async def of_app(self, org_id: str, app_id: str) -> tuple[bool, str | None]:
        """Whether the app exists in the organisation, and its profile."""
        async with self.session_scope(org_id) as session:
            app = await session.get(OrgApp, app_id)
            return (app is not None, app.vm_profile_id if app is not None else None)


class PlatformVmCapsDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    async def get(self) -> PlatformVmCaps | None:
        async with self.session_scope() as session:
            return await session.get(PlatformVmCaps, 1)

    async def put(self, caps: PlatformVmCaps) -> PlatformVmCaps:
        async with self.session_scope() as session:
            caps.id = 1
            caps.updated_at = datetime.now(timezone.utc)
            return await session.merge(caps)


class OrgVmQuotaDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    async def get(self, org_id: str) -> OrgVmQuota | None:
        async with self.session_scope() as session:
            return await session.get(OrgVmQuota, org_id)

    async def put(self, quota: OrgVmQuota) -> OrgVmQuota:
        async with self.session_scope() as session:
            quota.updated_at = datetime.now(timezone.utc)
            return await session.merge(quota)
