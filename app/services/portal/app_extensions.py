"""Which extensions an app has on: its own list, off until an org admin switches each on, like a group's."""

from __future__ import annotations

from app.api.base_v1.schema.portal.groups import GroupExtension, GroupExtensionList
from app.db.sql.apps import AppExtensionsDB
from app.db.sql.service_extensions import ServiceExtensionDB
from app.services.portal.app_errors import ExtensionNotShipped
from app.services.portal.apps import OrgApps


class AppExtensions:
    """An app's extensions, as its organisation's admins switch them."""

    def __init__(
        self,
        *,
        apps: OrgApps | None = None,
        extensions: AppExtensionsDB | None = None,
        shipped: ServiceExtensionDB | None = None,
    ) -> None:
        self._apps = apps or OrgApps()
        self._extensions = extensions or AppExtensionsDB()
        self._shipped = shipped or ServiceExtensionDB()

    async def all(self, org_id: str, app_id: str) -> GroupExtensionList:
        """Every approved extension, each with whether this app has it on."""
        await self._apps.client(org_id, app_id)
        on = await self._extensions.enabled_in(org_id, app_id)
        return GroupExtensionList(
            extensions=[
                GroupExtension(
                    extension_id=extension.extension_id,
                    name=str(extension.manifest.get('name') or extension.extension_id),
                    description=str(extension.manifest.get('description') or ''),
                    service_url=service.base_url,
                    enabled=extension.extension_id in on,
                )
                for extension, service in await self._shipped.distributed()
            ]
        )

    async def set(self, org_id: str, app_id: str, *, extension_id: str, enabled: bool, by_user_id: str) -> None:
        await self._apps.client(org_id, app_id)
        if not enabled:
            await self._extensions.disable(org_id, app_id, extension_id)
            return
        if await self._shipped.archive_of(extension_id) is None:
            raise ExtensionNotShipped()
        await self._extensions.enable(org_id, app_id, extension_id, by_user_id)
