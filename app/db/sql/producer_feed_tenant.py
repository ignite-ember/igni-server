"""The feed's reads in an organisation's own database: its marks, and what decides its producers' state."""

from __future__ import annotations

from sqlalchemy import delete, select

from app.db.sql.bind_chunks import chunks
from app.db.sql.models.org_app import OrgApp
from app.db.sql.models.org_group_member import OrgGroupMember
from app.db.sql.models.producer_feed import TenantProducerFeedMark
from app.db.sql.models.vm_credential import VmCredential
from app.db.sql.schemas.producer_feed import FeedMark
from app.db.sql.tenancy import TenantData


class ProducerFeedTenantDB(TenantData):
    async def marks(self, org_id: str) -> list[FeedMark]:
        async with self.session_scope(org_id) as session:
            rows = await session.execute(select(TenantProducerFeedMark))
            return [
                FeedMark(org_id=org_id, producer_kind=m.producer_kind, producer_id=m.producer_id, n=m.n)
                for m in rows.scalars().all()
            ]

    async def forget(self, org_id: str, marks: list[FeedMark]) -> None:
        """Delete the marks read, unless marked again since."""
        async with self.session_scope(org_id) as session:
            for mark in marks:
                await session.execute(
                    delete(TenantProducerFeedMark).where(
                        TenantProducerFeedMark.producer_kind == mark.producer_kind,
                        TenantProducerFeedMark.producer_id == mark.producer_id,
                        TenantProducerFeedMark.n == mark.n,
                    )
                )

    async def groups_of(self, org_id: str, user_ids: list[str]) -> dict[str, str]:
        """Each of these people's group, for those in one."""
        found: dict[str, str] = {}
        async with self.session_scope(org_id) as session:
            for part in chunks(user_ids):
                rows = await session.execute(
                    select(OrgGroupMember.user_id, OrgGroupMember.group_id).where(OrgGroupMember.user_id.in_(part))
                )
                found.update({str(user_id): str(group_id) for user_id, group_id in rows.all()})
        return found

    async def apps(self, org_id: str, app_ids: list[str] | None) -> dict[str, str | None]:
        """The organisation's apps among these (all, for None), with each one's profile."""
        found: dict[str, str | None] = {}
        async with self.session_scope(org_id) as session:
            if app_ids is None:
                rows = await session.execute(select(OrgApp.app_id, OrgApp.vm_profile_id))
                return {str(app_id): profile for app_id, profile in rows.all()}
            for part in chunks(app_ids):
                rows = await session.execute(select(OrgApp.app_id, OrgApp.vm_profile_id).where(OrgApp.app_id.in_(part)))
                found.update({str(app_id): profile for app_id, profile in rows.all()})
        return found

    async def with_credential(self, org_id: str, kind: str, producer_ids: list[str]) -> set[str]:
        """Which of these producers hold a working VM credential."""
        found: set[str] = set()
        async with self.session_scope(org_id) as session:
            for part in chunks(producer_ids):
                rows = await session.execute(
                    select(VmCredential.producer_id).where(
                        VmCredential.producer_kind == kind,
                        VmCredential.producer_id.in_(part),
                        VmCredential.revoked_at.is_(None),
                    )
                )
                found.update(str(pid) for pid in rows.scalars().all())
        return found
