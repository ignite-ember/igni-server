"""VM profile requests: an app or an extension asks; an org admin decides.

A VM runs nothing as root that an org admin has not approved, and a
profile's packages and setup script run as root when its layer is built.
So neither an app nor an extension sets its own profile: each **asks**.

* **An app** asks with its own token, for itself only (`{app_id}` is the
  token's `sub`, as for its spaces, §17.1). A service's token cannot ask
  for an app: a service is the deployment's and owns no app.
* **An extension** asks through its manifest's `vm` section — part of the
  signed bundle its service ships (`ExtensionVmRequests`).

A request changes no VM. An org admin (or a super admin, as for profiles)
approves the exact version they read — it becomes a profile, within the
platform's caps; an app's is assigned to the app, an extension's the
admin assigns to the groups and apps that use it — or rejects it. A
changed request is pending again, and the profile keeps what was last
approved until it is decided (`docs/service-identity.md` §12.3).

Refusals are the typed errors of `vm_profile_request_errors`;
`VmProfileRequestRefusals` turns them into HTTP answers.
"""

from __future__ import annotations

from app.api.base_v1.schema.vm_profile_requests import VmProfileRequestRecord, VmProfileRequestSettings
from app.api.base_v1.schema.vm_profiles import ProducerVmProfile, VmProfileSettings
from app.db.sql.models.registered_client import APP
from app.db.sql.models.vm_profile_request import APPROVED, PENDING, REJECTED
from app.db.sql.registered_clients import RegisteredClientDB
from app.db.sql.vm_profile_requests import VmProfileRequestsDB
from app.db.sql.vm_profiles import VmProfilesDB
from app.security.org_authority import OrgAuthority
from app.security.producer import ProducerCaller, ProducerRef
from app.services.cloud.extension_vm_requests import ExtensionVmRequests
from app.services.cloud.vm_profile_request_errors import (
    AlreadyApproved,
    AlreadyDecided,
    BeyondCaps,
    NoAppRequest,
    NoSuchRequest,
    NotTheApp,
    ProfileNamesTaken,
    RequestChanged,
)
from app.services.cloud.vm_profile_request_records import EXTENSION, ORG_ADMIN, SUPER_ADMIN, VmProfileRequestRecords
from app.services.cloud.vm_profiles import VmCaps, VmProfiles

#: The longest profile name, and how many numbered names an approval tries before giving up.
_NAME_LENGTH = 60
_NAME_ATTEMPTS = 20


class VmProfileRequests:
    """Asking, reading and deciding requests, in one organisation at a time."""

    def __init__(
        self,
        *,
        db: VmProfileRequestsDB | None = None,
        caps: VmCaps | None = None,
        profiles: VmProfiles | None = None,
        profiles_db: VmProfilesDB | None = None,
        clients: RegisteredClientDB | None = None,
        extensions: ExtensionVmRequests | None = None,
    ) -> None:
        self._db = db or VmProfileRequestsDB()
        self._caps = caps or VmCaps()
        self._profiles = profiles or VmProfiles()
        self._profiles_db = profiles_db or VmProfilesDB()
        self._clients = clients or RegisteredClientDB()
        self._extensions = extensions or ExtensionVmRequests(
            db=self._db, profiles_db=self._profiles_db, caps=self._caps
        )

    # ── Asking, for an app ──────────────────────────────────────────

    @staticmethod
    def _own(caller: ProducerCaller, app_id: str) -> str:
        """The app's organisation, when the caller is that app."""
        if caller.kind != APP or caller.id != app_id or not caller.org_id:
            raise NotTheApp()
        return caller.org_id

    async def ask_for_app(
        self, caller: ProducerCaller, *, app_id: str, settings: VmProfileRequestSettings
    ) -> tuple[VmProfileRequestRecord, bool]:
        """The app's request, and whether it is new or changed (pending) rather than the same as before."""
        org_id = self._own(caller, app_id)
        caps = await self._caps.get()
        refusal = VmCaps.refusal(settings, caps)
        if refusal is not None:
            raise BeyondCaps(refusal)
        row, changed = await self._db.ask(
            org_id,
            subject_kind=APP,
            subject_id=app_id,
            settings=settings.model_dump(mode='json'),
            version=VmProfileRequestRecords.version_of(settings),
            requested_by_kind=APP,
            requested_by_id=app_id,
        )
        return VmProfileRequestRecords.record(org_id, row=row, name=caller.name, caps=caps), changed

    async def of_app(self, caller: ProducerCaller, app_id: str) -> VmProfileRequestRecord:
        org_id = self._own(caller, app_id)
        row = await self._db.of(org_id, APP, app_id)
        if row is None:
            raise NoAppRequest()
        return VmProfileRequestRecords.record(org_id, row=row, name=caller.name, caps=await self._caps.get())

    async def app_profile(self, caller: ProducerCaller, app_id: str) -> ProducerVmProfile:
        """What the app's VM gets now — whatever is pending."""
        org_id = self._own(caller, app_id)
        return await self._profiles.for_producer(org_id, ProducerRef(kind=APP, id=app_id))

    # ── Reading, for the org admin ──────────────────────────────────

    async def all(self, org_id: str) -> list[VmProfileRequestRecord]:
        """Every request: pending first, then the newest."""
        extensions = await self._extensions.sync(org_id)
        apps = {client.client_id: client.name for client in await self._clients.apps_of(org_id)}
        names = {APP: apps, EXTENSION: extensions}
        caps = await self._caps.get()
        records = [
            VmProfileRequestRecords.record(org_id, row=row, name=names[row.subject_kind][row.subject_id], caps=caps)
            for row in await self._db.all(org_id)
            if row.subject_id in names[row.subject_kind]
        ]
        return sorted(records, key=lambda r: r.state != PENDING)

    async def _one(self, org_id: str, request_id: str) -> VmProfileRequestRecord:
        for record in await self.all(org_id):
            if record.id == request_id:
                return record
        raise NoSuchRequest()

    # ── Deciding ────────────────────────────────────────────────────

    @staticmethod
    def _acting_as(authority: OrgAuthority) -> str:
        return ORG_ADMIN if authority.is_org_admin else SUPER_ADMIN

    async def approve(self, authority: OrgAuthority, *, request_id: str, version: str) -> VmProfileRequestRecord:
        """Make the request a profile — exactly the version the admin read — and give an app's to the app."""
        org_id = authority.org_id
        request = await self._one(org_id, request_id)
        if request.version != version:
            raise RequestChanged()
        if request.state == APPROVED:
            raise AlreadyApproved()
        refusal = VmCaps.refusal(request.requested, await self._caps.get())
        if refusal is not None:
            raise BeyondCaps(refusal)
        profile_id = await self._make_profile(authority, request)
        if request.subject.kind == APP:
            await self._profiles.assign_app(org_id, request.subject.id, profile_id)
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
        existing = await self._profiles_db.get(authority.org_id, request.profile_id) if request.profile_id else None
        if existing is not None and not existing.is_default:
            settings = VmProfileSettings(name=existing.name, **request.requested.model_dump())
            await self._profiles.replace(authority.org_id, existing.id, settings, by_user_id=authority.user_id)
            return existing.id
        name = await self._free_name(authority.org_id, request.subject.profile_name()[:_NAME_LENGTH])
        made = await self._profiles.create(
            authority.org_id,
            VmProfileSettings(name=name, **request.requested.model_dump()),
            by_user_id=authority.user_id,
        )
        return made.id

    async def _free_name(self, org_id: str, base: str) -> str:
        """`base`, or `base (2)`, `base (3)`… — the first no profile in the organisation has."""
        taken = {profile.name for profile in await self._profiles_db.all(org_id)}
        for attempt in range(1, _NAME_ATTEMPTS + 1):
            suffix = '' if attempt == 1 else f' ({attempt})'
            name = base[: _NAME_LENGTH - len(suffix)] + suffix
            if name not in taken:
                return name
        raise ProfileNamesTaken(base)

    async def reject(self, authority: OrgAuthority, *, request_id: str, reason: str | None) -> VmProfileRequestRecord:
        """Say no; the running profile stays what it is."""
        request = await self._one(authority.org_id, request_id)
        if request.state != PENDING:
            raise AlreadyDecided(request.state)
        await self._db.decide(
            authority.org_id,
            request_id,
            state=REJECTED,
            by_user_id=authority.user_id,
            acting_as=self._acting_as(authority),
            reason=reason,
        )
        return await self._one(authority.org_id, request_id)
