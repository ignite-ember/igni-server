"""An extension's VM profile request: asked through its manifest, one per organisation.

An extension is the deployment's and is switched on per group and per app
in each organisation, so its request is one per organisation: kept the
first time anything there asks, and changed when a new version's `vm`
differs. The service that ships it reads it, and what an approval made
(`docs/service-identity.md` §12.3).
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from app.api.base_v1.schema.vm_profile_requests import (
    AssignedCounts,
    ManifestVm,
    ServiceVmProfile,
    VmProfileRequestSettings,
)
from app.db.sql.models.service_extension import ServiceExtension
from app.db.sql.models.vm_profile_request import VmProfileRequest
from app.db.sql.service_extensions import ServiceExtensionDB
from app.db.sql.vm_profile_requests import VmProfileRequestsDB
from app.db.sql.vm_profiles import VmProfilesDB
from app.security.service_caller import ServiceCaller
from app.services.cloud.vm_profile_request_records import EXTENSION, SERVICE, VmProfileRequestRecords
from app.services.cloud.vm_profiles import VmCaps, VmProfiles

logger = logging.getLogger(__name__)


class ExtensionVmRequests:
    """What the distributed extensions ask their VMs to get, kept in one organisation at a time."""

    def __init__(
        self,
        *,
        db: VmProfileRequestsDB | None = None,
        extensions: ServiceExtensionDB | None = None,
        profiles_db: VmProfilesDB | None = None,
        caps: VmCaps | None = None,
    ) -> None:
        self._db = db or VmProfileRequestsDB()
        self._extensions = extensions or ServiceExtensionDB()
        self._profiles_db = profiles_db or VmProfilesDB()
        self._caps = caps or VmCaps()

    @staticmethod
    def manifest_vm(extension: ServiceExtension) -> VmProfileRequestSettings | None:
        """What the extension's manifest asks for; None for nothing (or, never stored, unreadable)."""
        declared = (extension.manifest or {}).get('vm')
        if not declared:
            return None
        try:
            return ManifestVm.model_validate(declared).settings()
        except ValidationError:
            logger.warning('extension %s: its manifest `vm` is unreadable; asking for nothing', extension.extension_id)
            return None

    @staticmethod
    def name_of(extension: ServiceExtension) -> str:
        return str(extension.manifest.get('name') or extension.extension_id)

    async def _ask(
        self, org_id: str, *, extension: ServiceExtension, settings: VmProfileRequestSettings
    ) -> VmProfileRequest:
        row, _ = await self._db.ask(
            org_id,
            subject_kind=EXTENSION,
            subject_id=extension.extension_id,
            settings=settings.model_dump(mode='json'),
            version=VmProfileRequestRecords.version_of(settings),
            requested_by_kind=SERVICE,
            requested_by_id=extension.client_id,
        )
        return row

    async def sync(self, org_id: str) -> dict[str, str]:
        """Bring the organisation's extension requests in line with what the distributed extensions ask.

        Answers each asking extension's name, by id. An extension that
        asks for nothing any more — its service gone, or a version without
        `vm` — has its request forgotten; a profile an approval made stays
        the organisation's.
        """
        names: dict[str, str] = {}
        for extension, _client in await self._extensions.distributed():
            settings = self.manifest_vm(extension)
            if settings is None:
                continue
            await self._ask(org_id, extension=extension, settings=settings)
            names[extension.extension_id] = self.name_of(extension)
        await self._db.keep_only(org_id, EXTENSION, list(names))
        return names

    async def for_service(self, caller: ServiceCaller) -> ServiceVmProfile:
        """The service's extension's request in the token's organisation, and what an approval made."""
        org_id = caller.org_id
        extension = await self._extensions.of(caller.client_id)
        if extension is None:
            return ServiceVmProfile(org_id=org_id)
        settings = self.manifest_vm(extension)
        if settings is None:
            return ServiceVmProfile(org_id=org_id, extension_id=extension.extension_id)
        caps = await self._caps.get()
        row = await self._ask(org_id, extension=extension, settings=settings)
        answer = ServiceVmProfile(
            org_id=org_id,
            extension_id=extension.extension_id,
            request=VmProfileRequestRecords.record(org_id, row=row, name=self.name_of(extension), caps=caps),
        )
        profile = await self._profiles_db.get(org_id, row.profile_id) if row.profile_id else None
        if profile is not None:
            answer.profile = VmProfiles.served(profile, VmCaps.clamped(VmProfiles.settings_of(profile), caps))
            answer.assigned = AssignedCounts(
                groups=sum(1 for group in await self._profiles_db.groups(org_id) if group.vm_profile_id == profile.id),
                apps=sum(1 for app in await self._profiles_db.apps(org_id) if app.vm_profile_id == profile.id),
            )
        return answer
