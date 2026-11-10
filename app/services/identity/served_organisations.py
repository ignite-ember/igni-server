"""The organisations a service serves: where its extension is switched on (`docs/service-identity.md` §21).

So a service's SDK can follow every one of them from the start — their
revocations above all — rather than learning of each at its first token.
"""

from __future__ import annotations

from app.api.base_v1.schema.served import ServedOrganisation, ServedOrganisations
from app.db.sql.apps import AppExtensionsDB
from app.db.sql.group_extensions import GroupExtensionsDB
from app.db.sql.organizations import OrganizationDB
from app.db.sql.service_extensions import ServiceExtensionDB
from app.db.sql.tenant_registry import TenantRegistryDB


class Served:
    def __init__(self, *, tenants: TenantRegistryDB | None = None) -> None:
        self._tenants = tenants or TenantRegistryDB()

    async def org_ids(self, client_id: str) -> list[str]:
        """Each organisation with the service's extension on for a group or an app."""
        extension = await ServiceExtensionDB().of(client_id)
        if extension is None:
            return []
        served = []
        for org_id in await self._tenants.active_org_ids():
            if extension.extension_id in await GroupExtensionsDB().groups_enabling(org_id) or (
                extension.extension_id in await AppExtensionsDB().apps_enabling(org_id)
            ):
                served.append(org_id)
        return served

    async def list(self, client_id: str) -> ServedOrganisations:
        organisations = []
        for org_id in await self.org_ids(client_id):
            org = await OrganizationDB().get_by_id(org_id)
            if org is not None:
                organisations.append(ServedOrganisation(org_id=org_id, name=str(org.display_name or org.slug)))
        return ServedOrganisations(organisations=organisations)
