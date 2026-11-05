"""VM profile requests: an app or an extension asks; an org admin decides.

A VM runs nothing as root that an org admin has not approved, and a
profile's packages and setup script run as root when its layer is built.
So neither an app nor an extension sets its own profile: each **asks**.

* **An app** asks with its own token, for itself only (`{app_id}` is the
  token's `sub`, as for its spaces, §17.1). A service's token cannot ask
  for an app: a service is the deployment's and owns no app.
* **An extension** asks through its manifest's `vm` section — part of the
  signed bundle its service ships. An extension is the deployment's and is
  switched on per group and per app in each organisation, so its request
  is one per organisation: kept the first time anything there asks, and
  changed when a new version's `vm` differs.

A request changes no VM. An org admin (or a super admin, as for profiles)
approves the exact version they read — it becomes a profile, within the
platform's caps; an app's is assigned to the app, an extension's the
admin assigns to the groups and apps that use it — or rejects it. A
changed request is pending again, and the profile keeps what was last
approved until it is decided (`docs/service-identity.md` §12.3).
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from pydantic import ValidationError

from app.api.base_v1.schema.vm_commands import Requester
from app.api.base_v1.schema.vm_profile_requests import (
    AssignedCounts,
    ManifestVm,
    RequestedBy,
    RequestSubject,
    ServiceVmProfile,
    VmProfileRequestRecord,
    VmProfileRequestSettings,
)
from app.api.base_v1.schema.vm_profiles import PlatformVmCapsBody, ProducerVmProfile, VmProfileSettings
from app.db.sql.models.registered_client import APP
from app.db.sql.models.service_extension import ServiceExtension
from app.db.sql.models.vm_profile_request import APPROVED, PENDING, REJECTED, VmProfileRequest
from app.db.sql.registered_clients import RegisteredClientDB
from app.db.sql.service_extensions import ServiceExtensionDB
from app.db.sql.vm_profile_requests import VmProfileRequestsDB
from app.db.sql.vm_profiles import VmProfilesDB
from app.security.org_authority import OrgAuthority
from app.security.producer import ProducerCaller, ProducerRef
from app.security.service_caller import ServiceCaller
from app.services.cloud.vm_profiles import ProfileVersion, VmCaps, VmProfiles

logger = logging.getLogger(__name__)

EXTENSION = 'extension'
SERVICE = 'service'
ORG_ADMIN = 'org_admin'
SUPER_ADMIN = 'super_admin'

#: How a profile an approval makes is named, before a number is added for a name taken.
_PROFILE_NAME = {APP: 'App: {}', EXTENSION: 'Extension: {}'}
_NAME_ATTEMPTS = 20

#: Fields a request gained after t0024, and their value before: kept out of its version while they have it.
_LATER_FIELDS: dict[str, object] = {'internet': False}


class VmProfileRequests:
    """Asking, reading and deciding requests, in one organisation at a time."""

    def __init__(self) -> None:
        self._db = VmProfileRequestsDB()
        self._caps = VmCaps()

    # ── Asking ──────────────────────────────────────────────────────

    @staticmethod
    def _own(caller: ProducerCaller, app_id: str) -> str:
        """The app's organisation, when the caller is that app; a 403 otherwise."""
        if caller.kind != APP or caller.id != app_id or not caller.org_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Only the app itself asks for its VM's profile"
            )
        return caller.org_id

    async def ask_for_app(
        self, caller: ProducerCaller, app_id: str, settings: VmProfileRequestSettings
    ) -> tuple[VmProfileRequestRecord, bool]:
        """The app's request, and whether it is new or changed (pending) rather than the same as before."""
        org_id = self._own(caller, app_id)
        caps = await self._caps.get()
        refusal = VmCaps.refusal(settings, caps)
        if refusal is not None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=refusal)
        row, changed = await self._db.ask(
            org_id,
            subject_kind=APP,
            subject_id=app_id,
            settings=settings.model_dump(mode='json'),
            version=self.version_of(settings),
            requested_by_kind=APP,
            requested_by_id=app_id,
        )
        return self._record(org_id, row, caller.name, caps), changed

    async def of_app(self, caller: ProducerCaller, app_id: str) -> VmProfileRequestRecord:
        org_id = self._own(caller, app_id)
        row = await self._db.of(org_id, APP, app_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='This app has asked for no VM profile')
        return self._record(org_id, row, caller.name, await self._caps.get())

    async def app_profile(self, caller: ProducerCaller, app_id: str) -> ProducerVmProfile:
        """What the app's VM gets now — whatever is pending."""
        org_id = self._own(caller, app_id)
        return await VmProfiles().for_producer(org_id, ProducerRef(kind=APP, id=app_id))

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

    async def _ask_for_extension(
        self, org_id: str, extension: ServiceExtension, settings: VmProfileRequestSettings
    ) -> VmProfileRequest:
        row, _ = await self._db.ask(
            org_id,
            subject_kind=EXTENSION,
            subject_id=extension.extension_id,
            settings=settings.model_dump(mode='json'),
            version=self.version_of(settings),
            requested_by_kind=SERVICE,
            requested_by_id=extension.client_id,
        )
        return row

    async def sync_extensions(self, org_id: str) -> dict[str, str]:
        """Bring the organisation's extension requests in line with what the distributed extensions ask.

        Answers each asking extension's name, by id. An extension that
        asks for nothing any more — its service gone, or a version without
        `vm` — has its request forgotten; a profile an approval made stays
        the organisation's.
        """
        names: dict[str, str] = {}
        for extension, _client in await ServiceExtensionDB().distributed():
            settings = self.manifest_vm(extension)
            if settings is None:
                continue
            await self._ask_for_extension(org_id, extension, settings)
            names[extension.extension_id] = str(extension.manifest.get('name') or extension.extension_id)
        await self._db.keep_only(org_id, EXTENSION, list(names))
        return names

    # ── Reading, for the org admin ──────────────────────────────────

    async def all(self, org_id: str) -> list[VmProfileRequestRecord]:
        """Every request: pending first, then the newest."""
        extensions = await self.sync_extensions(org_id)
        apps = {client.client_id: client.name for client in await RegisteredClientDB().apps_of(org_id)}
        caps = await self._caps.get()
        records = [
            self._record(org_id, row, (apps if row.subject_kind == APP else extensions)[row.subject_id], caps)
            for row in await self._db.all(org_id)
            if row.subject_id in (apps if row.subject_kind == APP else extensions)
        ]
        return sorted(records, key=lambda r: r.state != PENDING)

    async def _one(self, org_id: str, request_id: str) -> VmProfileRequestRecord:
        for record in await self.all(org_id):
            if record.id == request_id:
                return record
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No such VM profile request')

    # ── Deciding ────────────────────────────────────────────────────

    @staticmethod
    def _acting_as(authority: OrgAuthority) -> str:
        return ORG_ADMIN if authority.is_org_admin else SUPER_ADMIN

    async def approve(self, authority: OrgAuthority, request_id: str, version: str) -> VmProfileRequestRecord:
        """Make the request a profile — exactly the version the admin read — and give an app's to the app."""
        org_id = authority.org_id
        request = await self._one(org_id, request_id)
        if request.version != version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail='The request has changed since it was read: read it again before approving',
            )
        if request.state == APPROVED:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='This request is approved already')
        refusal = VmCaps.refusal(request.requested, await self._caps.get())
        if refusal is not None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=refusal)
        profile_id = await self._make_profile(authority, request)
        if request.subject.kind == APP:
            await VmProfiles().assign_app(org_id, request.subject.id, profile_id)
        await self._db.decide(
            org_id,
            request_id,
            state=APPROVED,
            by_user_id=authority.user_id,
            acting_as=self._acting_as(authority),
            profile_id=profile_id,
        )
        return await self._one(org_id, request_id)

    async def _make_profile(self, authority: OrgAuthority, request: VmProfileRequestRecord) -> str:
        """The profile an earlier approval made, replaced in place; or a new one."""
        profiles = VmProfiles()
        existing = await VmProfilesDB().get(authority.org_id, request.profile_id) if request.profile_id else None
        if existing is not None and not existing.is_default:
            settings = VmProfileSettings(name=existing.name, **request.requested.model_dump())
            await profiles.replace(authority.org_id, existing.id, settings, by_user_id=authority.user_id)
            return existing.id
        base = _PROFILE_NAME[request.subject.kind].format(request.subject.name)[:60]
        for attempt in range(1, _NAME_ATTEMPTS + 1):
            suffix = '' if attempt == 1 else f' ({attempt})'
            name = base[: 60 - len(suffix)] + suffix
            try:
                made = await profiles.create(
                    authority.org_id,
                    VmProfileSettings(name=name, **request.requested.model_dump()),
                    by_user_id=authority.user_id,
                )
            except HTTPException as exc:
                if exc.status_code == status.HTTP_409_CONFLICT:
                    continue
                raise
            return made.id
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Profiles named '{base}' are all taken: rename one"
        )

    async def reject(self, authority: OrgAuthority, request_id: str, reason: str | None) -> VmProfileRequestRecord:
        """Say no; the running profile stays what it is."""
        request = await self._one(authority.org_id, request_id)
        if request.state != PENDING:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f'This request is {request.state} already')
        await self._db.decide(
            authority.org_id,
            request_id,
            state=REJECTED,
            by_user_id=authority.user_id,
            acting_as=self._acting_as(authority),
            reason=reason,
        )
        return await self._one(authority.org_id, request_id)

    # ── Reading, for the service that ships an extension ────────────

    async def for_service(self, caller: ServiceCaller) -> ServiceVmProfile:
        """The service's extension's request in the token's organisation, and what an approval made."""
        org_id = caller.org_id
        extension = await ServiceExtensionDB().of(caller.client_id)
        if extension is None:
            return ServiceVmProfile(org_id=org_id)
        settings = self.manifest_vm(extension)
        if settings is None:
            return ServiceVmProfile(org_id=org_id, extension_id=extension.extension_id)
        caps = await self._caps.get()
        row = await self._ask_for_extension(org_id, extension, settings)
        name = str(extension.manifest.get('name') or extension.extension_id)
        answer = ServiceVmProfile(
            org_id=org_id, extension_id=extension.extension_id, request=self._record(org_id, row, name, caps)
        )
        profile = await VmProfilesDB().get(org_id, row.profile_id) if row.profile_id else None
        if profile is not None:
            answer.profile = VmProfiles.served(profile, VmCaps.clamped(VmProfiles.settings_of(profile), caps))
            db = VmProfilesDB()
            answer.assigned = AssignedCounts(
                groups=sum(1 for group in await db.groups(org_id) if group.vm_profile_id == profile.id),
                apps=sum(1 for app in await db.apps(org_id) if app.vm_profile_id == profile.id),
            )
        return answer

    # ── Shapes ──────────────────────────────────────────────────────

    @staticmethod
    def version_of(settings: VmProfileRequestSettings) -> str:
        """A request's version. A field added after requests were first kept is left out while it has
        its value from before (`_LATER_FIELDS`), so the same request asked again after an upgrade keeps
        its version — and its decision — instead of going back to pending."""
        values = settings.model_dump(mode='json')
        for field, then in _LATER_FIELDS.items():
            if values.get(field) == then:
                values.pop(field)
        return ProfileVersion.digest({'requested': values})

    def _record(
        self, org_id: str, row: VmProfileRequest, name: str, caps: PlatformVmCapsBody
    ) -> VmProfileRequestRecord:
        requested = VmProfileRequestSettings.model_validate(row.settings)
        return VmProfileRequestRecord(
            id=row.id,
            org_id=org_id,
            subject=RequestSubject(kind=row.subject_kind, id=row.subject_id, name=name),  # type: ignore[arg-type]
            state=row.state,  # type: ignore[arg-type]
            requested=requested,
            version=row.version,
            packages_version=ProfileVersion.packages(requested),
            within_caps=VmCaps.refusal(requested, caps) is None,
            requested_by=RequestedBy(kind=row.requested_by_kind, id=row.requested_by_id),  # type: ignore[arg-type]
            requested_at=row.requested_at,
            decided_by=(
                Requester(kind='person', id=row.decided_by_user_id, acting_as=row.decided_as or ORG_ADMIN)
                if row.decided_by_user_id
                else None
            ),
            decided_at=row.decided_at,
            reason=row.reason,
            profile_id=row.profile_id,
        )
