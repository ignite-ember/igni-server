"""An organisation's apps and the extensions each has on.

In the organisation's own database. The app's name and keys are its
registered client on the shared plane (`RegisteredClientDB`); these
tables hold what belongs to the organisation (`docs/service-identity.md` §11).
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.db.sql.models.org_app import OrgApp
from app.db.sql.models.org_app_extension import OrgAppExtension
from app.db.sql.tenancy import TenantData


class AppsDB(TenantData):
    """The organisation's apps."""

    async def add(self, org_id: str, app: OrgApp) -> OrgApp:
        async with self.session_scope(org_id) as session:
            session.add(app)
        return app

    async def get(self, org_id: str, app_id: str) -> OrgApp | None:
        async with self.session_scope(org_id) as session:
            return await session.get(OrgApp, app_id)

    async def all(self, org_id: str) -> list[OrgApp]:
        async with self.session_scope(org_id) as session:
            rows = await session.execute(select(OrgApp).order_by(OrgApp.created_at))
            return list(rows.scalars().all())

    async def set_owner(self, org_id: str, app_id: str, owner_user_id: str) -> None:
        async with self.session_scope(org_id) as session:
            app = await session.get(OrgApp, app_id)
            if app is not None:
                app.owner_user_id = owner_user_id

    async def remove(self, org_id: str, app_id: str) -> None:
        async with self.session_scope(org_id) as session:
            await session.execute(delete(OrgApp).where(OrgApp.app_id == app_id))


class AppExtensionsDB(TenantData):
    """Per-app enablement of extensions, scoped to an organisation."""

    async def enabled_in(self, org_id: str, app_id: str) -> set[str]:
        async with self.session_scope(org_id) as session:
            rows = await session.execute(select(OrgAppExtension.extension_id).where(OrgAppExtension.app_id == app_id))
            return {str(extension_id) for extension_id in rows.scalars().all()}

    async def apps_enabling(self, org_id: str) -> dict[str, list[str]]:
        """Every extension switched on for an app of the organisation → those apps."""
        async with self.session_scope(org_id) as session:
            rows = await session.execute(
                select(OrgAppExtension.extension_id, OrgAppExtension.app_id).order_by(
                    OrgAppExtension.extension_id, OrgAppExtension.app_id
                )
            )
            apps: dict[str, list[str]] = defaultdict(list)
            for extension_id, app_id in rows.all():
                apps[str(extension_id)].append(str(app_id))
            return dict(apps)

    async def enable(self, org_id: str, app_id: str, extension_id: str, by_user_id: str | None) -> None:
        async with self.session_scope(org_id) as session:
            await session.execute(
                insert(OrgAppExtension)
                .values(app_id=app_id, extension_id=extension_id, enabled_by_user_id=by_user_id)
                .on_conflict_do_nothing(constraint='uq_org_app_extensions_app_extension')
            )

    async def disable(self, org_id: str, app_id: str, extension_id: str) -> None:
        async with self.session_scope(org_id) as session:
            await session.execute(
                delete(OrgAppExtension).where(
                    OrgAppExtension.app_id == app_id, OrgAppExtension.extension_id == extension_id
                )
            )
