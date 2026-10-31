"""Which extensions each group of an organisation has switched on.

In the organisation's own database, beside its groups. A row is "on";
deleting it is "off" (`docs/service-identity.md` §8.3).
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.db.sql.models.org_group_extension import OrgGroupExtension
from app.db.sql.tenancy import TenantData


class GroupExtensionsDB(TenantData):
    """Per-group enablement of extensions, scoped to an organisation."""

    async def enabled_in(self, org_id: str, group_id: str) -> set[str]:
        """The extension ids switched on for one group."""
        async with self.session_scope(org_id) as session:
            rows = await session.execute(
                select(OrgGroupExtension.extension_id).where(OrgGroupExtension.group_id == group_id)
            )
            return {str(extension_id) for extension_id in rows.scalars().all()}

    async def groups_enabling(self, org_id: str) -> dict[str, list[str]]:
        """Every extension switched on anywhere in the organisation → the groups it is on for."""
        async with self.session_scope(org_id) as session:
            rows = await session.execute(
                select(OrgGroupExtension.extension_id, OrgGroupExtension.group_id).order_by(
                    OrgGroupExtension.extension_id, OrgGroupExtension.group_id
                )
            )
            groups: dict[str, list[str]] = defaultdict(list)
            for extension_id, group_id in rows.all():
                groups[str(extension_id)].append(str(group_id))
            return dict(groups)

    async def enable(self, org_id: str, group_id: str, extension_id: str, by_user_id: str | None) -> None:
        """Switch it on. Switching on what is already on changes nothing."""
        async with self.session_scope(org_id) as session:
            await session.execute(
                insert(OrgGroupExtension)
                .values(group_id=group_id, extension_id=extension_id, enabled_by_user_id=by_user_id)
                .on_conflict_do_nothing(constraint='uq_org_group_extensions_group_extension')
            )

    async def disable(self, org_id: str, group_id: str, extension_id: str) -> None:
        """Switch it off. Switching off what is already off changes nothing."""
        async with self.session_scope(org_id) as session:
            await session.execute(
                delete(OrgGroupExtension).where(
                    OrgGroupExtension.group_id == group_id, OrgGroupExtension.extension_id == extension_id
                )
            )
