"""VM profiles: what each producer's microVM gets, and the limits on them.

An org admin defines named profiles — memory (the most a VM may have,
and what it normally has), vCPUs, how long idle before a pause (or never),
how long an app's unwoken VM is kept before it is erased, and the
packages built
into the profile's read-only layer — and assigns
one to each group and each app. A person gets their one group's; a group
or app with none gets the organisation's default profile. A super admin
sets the platform's caps, which every profile must fit, and may define
and assign profiles in any organisation (`docs/service-identity.md` §12).
"""

from __future__ import annotations

import hashlib
import json
import uuid

from fastapi import HTTPException, status

from app.api.base_v1.schema.effective_vm_profile import EffectiveVmProfile, VmPackages
from app.api.base_v1.schema.vm_profiles import (
    OrgVmQuotaBody,
    OrgVmQuotaResponse,
    PlatformVmCapsBody,
    PlatformVmCapsResponse,
    ProducerAssignment,
    ProducerVmProfile,
    VmProfileResponse,
    VmProfileSettings,
    VmProfilesOverview,
)
from app.config import settings
from app.db.sql.models.app_space import SPACE
from app.db.sql.models.org_vm_quota import OrgVmQuota
from app.db.sql.models.platform_vm_caps import PlatformVmCaps
from app.db.sql.models.registered_client import APP
from app.db.sql.models.vm_profile import VmProfile
from app.db.sql.registered_clients import RegisteredClientDB
from app.db.sql.spaces import SpacesDB
from app.db.sql.team import TeamDB
from app.db.sql.vm_profiles import OrgVmQuotaDB, PlatformVmCapsDB, VmProfileNameTaken, VmProfilesDB
from app.security.producer import ProducerRef
from app.services.cloud.volume_sizes import VolumeSizes
from app.services.portal.group_extensions import GroupExtensions


class VmDefaults:
    """The deployment's defaults (`settings.cloud_vm`): the caps until a super admin sets them, and the
    organisation's default profile when it is first made. By default 1 GiB normally (igni idle uses
    ~220 MB), up to 4 GiB while it needs more — igni and what it ran peaked at 1.9 GB building ripgrep
    from source; memory above the normal size costs the node nothing until a VM uses it (igni's
    `docs/CLOUD.md` §2).
    """

    @staticmethod
    def caps() -> PlatformVmCapsBody:
        c = settings.cloud_vm
        return PlatformVmCapsBody(
            max_memory_mib=c.cap_max_memory_mib,
            max_vcpus=c.cap_max_vcpus,
            max_idle_before_pause_s=c.cap_max_idle_before_pause_s,
            max_volume_gib=c.cap_max_volume_gib,
            max_erase_after_idle_days=c.cap_max_erase_after_idle_days,
        )

    @staticmethod
    def standard() -> VmProfileSettings:
        """Every field a profile body may leave out takes the Standard value already."""
        return VmProfileSettings(name=settings.cloud_vm.standard_name)


class VmCaps:
    """The platform's caps, and fitting a profile to them."""

    async def get(self) -> PlatformVmCapsResponse:
        row = await PlatformVmCapsDB().get()
        if row is None:
            return PlatformVmCapsResponse(**VmDefaults.caps().model_dump())
        return PlatformVmCapsResponse(
            max_memory_mib=row.max_memory_mib,
            max_vcpus=row.max_vcpus,
            max_idle_before_pause_s=row.max_idle_before_pause_s,
            max_volume_gib=row.max_volume_gib,
            max_erase_after_idle_days=row.max_erase_after_idle_days,
            updated_at=row.updated_at,
        )

    async def from_deployment(self) -> bool:
        """Whether the deployment's caps are in force: no super admin has set any."""
        return await PlatformVmCapsDB().get() is None

    async def set(self, body: PlatformVmCapsBody, *, by_user_id: str | None) -> PlatformVmCapsResponse:
        await PlatformVmCapsDB().put(PlatformVmCaps(**body.model_dump(), updated_by_user_id=by_user_id))
        return await self.get()

    @staticmethod
    def refusal(settings: VmProfileSettings, caps: PlatformVmCapsBody) -> str | None:
        """Why *settings* does not fit *caps*, or None."""
        if settings.memory_mib > caps.max_memory_mib:
            return f'The memory ceiling may be at most {caps.max_memory_mib} MiB on this platform'
        if settings.vcpus > caps.max_vcpus:
            return f'At most {caps.max_vcpus} vCPUs on this platform'
        longest = caps.max_idle_before_pause_s
        if longest is not None and (settings.idle_before_pause_s is None or settings.idle_before_pause_s > longest):
            return f'A VM may stay awake idle for at most {longest} seconds on this platform'
        if settings.volume_max_gib > caps.max_volume_gib:
            return f'A volume may grow to at most {caps.max_volume_gib} GiB on this platform'
        most_days = caps.max_erase_after_idle_days
        if most_days is not None and (
            settings.erase_after_idle_days is None or settings.erase_after_idle_days > most_days
        ):
            return f'A VM not woken for {most_days} days must have its workspace erased on this platform'
        return None

    @staticmethod
    def clamped(settings: VmProfileSettings, caps: PlatformVmCapsBody) -> VmProfileSettings:
        """*settings* as served: never more than the caps, which may have been lowered since it was saved."""
        idle = settings.idle_before_pause_s
        longest = caps.max_idle_before_pause_s
        if longest is not None:
            idle = longest if idle is None else min(idle, longest)
        memory = min(settings.memory_mib, caps.max_memory_mib)
        days = settings.erase_after_idle_days
        most_days = caps.max_erase_after_idle_days
        if most_days is not None:
            days = most_days if days is None else min(days, most_days)
        return settings.model_copy(
            update={
                'memory_mib': memory,
                'normal_memory_mib': min(settings.normal_memory_mib, memory),
                'expected_memory_mib': (
                    None if settings.expected_memory_mib is None else min(settings.expected_memory_mib, memory)
                ),
                'vcpus': min(settings.vcpus, caps.max_vcpus),
                'idle_before_pause_s': idle,
                'volume_max_gib': min(settings.volume_max_gib, caps.max_volume_gib),
                'volume_gib': min(settings.volume_gib, caps.max_volume_gib),
                'erase_after_idle_days': days,
            }
        )


class ProfileVersion:
    """Content hashes: equal exactly when what they cover is equal."""

    @staticmethod
    def _digest(value: object) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:32]

    #: Fields added after versions were first handed out, and their values
    #: then: left out of the digest while they have them, so adding a field
    #: does not change every producer's version (which the gateway would
    #: answer by re-provisioning every VM).
    LATER_FIELDS: dict[str, object] = {'erase_after_idle_days': None}

    @classmethod
    def of(cls, profile_id: str, effective: VmProfileSettings) -> str:
        values = effective.model_dump(mode='json')
        for field, then in cls.LATER_FIELDS.items():
            if values.get(field) == then:
                values.pop(field)
        return cls._digest({'id': profile_id, **values})

    @classmethod
    def packages(cls, effective: VmProfileSettings) -> str:
        return cls._digest({'packages': effective.packages.model_dump(), 'setup_script': effective.setup_script})


class VmProfiles:
    """One organisation's profiles, their assignment, and the profile a producer gets."""

    def __init__(self) -> None:
        self._db = VmProfilesDB()
        self._caps = VmCaps()

    @staticmethod
    def settings_of(profile: VmProfile) -> VmProfileSettings:
        return VmProfileSettings(
            name=profile.name,
            memory_mib=profile.memory_mib,
            normal_memory_mib=profile.normal_memory_mib,
            vcpus=profile.vcpus,
            idle_before_pause_s=profile.idle_before_pause_s,
            expected_memory_mib=profile.expected_memory_mib,
            packages=VmPackages.model_validate(profile.packages or {}),
            setup_script=profile.setup_script,
            volume_gib=profile.volume_gib,
            volume_max_gib=profile.volume_max_gib,
            erase_after_idle_days=profile.erase_after_idle_days,
        )

    @staticmethod
    def served(profile: VmProfile, effective: VmProfileSettings) -> EffectiveVmProfile:
        """What a VM gets from *profile*, given its values as served (*effective*, clamped to the caps)."""
        return EffectiveVmProfile(
            profile_id=profile.id,
            profile_name=profile.name,
            memory_mib=effective.memory_mib,
            normal_memory_mib=effective.normal_memory_mib,
            expected_memory_mib=effective.expected_memory_mib,
            vcpus=effective.vcpus,
            idle_before_pause_s=effective.idle_before_pause_s,
            packages=effective.packages,
            setup_script=effective.setup_script,
            volume_max_gib=effective.volume_max_gib,
            erase_after_idle_days=effective.erase_after_idle_days,
        )

    def response(self, profile: VmProfile, caps: PlatformVmCapsBody) -> VmProfileResponse:
        settings = self.settings_of(profile)
        effective = VmCaps.clamped(settings, caps)
        return VmProfileResponse(
            **settings.model_dump(),
            id=profile.id,
            is_default=profile.is_default,
            version=ProfileVersion.of(profile.id, effective),
            packages_version=ProfileVersion.packages(effective),
            within_caps=VmCaps.refusal(settings, caps) is None,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )

    async def default(self, org_id: str) -> VmProfile:
        return await self._db.default(
            org_id,
            VmProfile(
                id=str(uuid.uuid4()),
                **(standard := VmDefaults.standard()).model_dump(exclude={'packages'}),
                packages=standard.packages.model_dump(),
            ),
        )

    async def _profile(self, org_id: str, profile_id: str) -> VmProfile:
        found = await self._db.get(org_id, profile_id)
        if found is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='VM profile not found')
        return found

    async def _fits(self, settings: VmProfileSettings) -> None:
        reason = VmCaps.refusal(settings, await self._caps.get())
        if reason is not None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason)

    @staticmethod
    def _taken(name: str) -> HTTPException:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"A VM profile named '{name}' already exists")

    async def overview(self, org_id: str) -> VmProfilesOverview:
        default = await self.default(org_id)
        caps = await self._caps.get()
        clients = {client.client_id: client.name for client in await RegisteredClientDB().apps_of(org_id)}
        return VmProfilesOverview(
            org_id=org_id,
            caps=caps,
            quota=await VmQuotas().get(org_id),
            default_profile_id=default.id,
            profiles=[self.response(profile, caps) for profile in await self._db.all(org_id)],
            groups=[
                ProducerAssignment(id=group.id, name=group.name, vm_profile_id=group.vm_profile_id)
                for group in await self._db.groups(org_id)
            ],
            apps=[
                ProducerAssignment(
                    id=app.app_id, name=clients.get(app.app_id, app.app_id), vm_profile_id=app.vm_profile_id
                )
                for app in await self._db.apps(org_id)
            ],
        )

    async def create(self, org_id: str, settings: VmProfileSettings, *, by_user_id: str) -> VmProfileResponse:
        await self._fits(settings)
        await self.default(org_id)
        try:
            profile = await self._db.add(
                org_id,
                VmProfile(
                    **settings.model_dump(exclude={'packages'}),
                    packages=settings.packages.model_dump(),
                    created_by_user_id=by_user_id,
                    updated_by_user_id=by_user_id,
                ),
            )
        except VmProfileNameTaken as exc:
            raise self._taken(settings.name) from exc
        return self.response(profile, await self._caps.get())

    async def replace(
        self, org_id: str, profile_id: str, settings: VmProfileSettings, *, by_user_id: str
    ) -> VmProfileResponse:
        await self._profile(org_id, profile_id)
        await self._fits(settings)
        try:
            profile = await self._db.replace(
                org_id,
                profile_id,
                {**settings.model_dump(exclude={'packages'}), 'packages': settings.packages.model_dump()},
                by_user_id,
            )
        except VmProfileNameTaken as exc:
            raise self._taken(settings.name) from exc
        return self.response(profile, await self._caps.get())

    async def delete(self, org_id: str, profile_id: str) -> None:
        profile = await self._profile(org_id, profile_id)
        if profile.is_default:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail='The default profile cannot be deleted: every group and app without one gets it',
            )
        await self._db.remove(org_id, profile_id)

    async def _assignable(self, org_id: str, profile_id: str | None) -> None:
        if profile_id is not None:
            await self._profile(org_id, profile_id)

    async def assign_group(self, org_id: str, group_id: str, profile_id: str | None) -> None:
        await self._assignable(org_id, profile_id)
        if not await self._db.assign_group(org_id, group_id, profile_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Group not found')

    async def assign_app(self, org_id: str, app_id: str, profile_id: str | None) -> None:
        await self._assignable(org_id, profile_id)
        if not await self._db.assign_app(org_id, app_id, profile_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='App not found')

    async def assigned(self, org_id: str, producer: ProducerRef) -> str | None:
        """The profile id assigned to the producer's group or app; None for the default. 404 for no such producer."""
        if producer.kind == SPACE:
            # A space's VM gets its app's profile.
            space = await SpacesDB().get(org_id, producer.id)
            if space is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No such space in this organisation')
            producer = ProducerRef(kind=APP, id=space.app_id)
        if producer.kind == APP:
            exists, profile_id = await self._db.of_app(org_id, producer.id)
            if not exists:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No such app in this organisation')
            return profile_id
        if await TeamDB().get_active_member(org_id, producer.id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail='No such active member in this organisation'
            )
        group = await GroupExtensions().group_of(producer.id, org_id)
        return await self._db.of_group(org_id, group) if group else None

    async def for_producer(self, org_id: str, producer: ProducerRef) -> ProducerVmProfile:
        """What the producer's VM gets: their group's (a person) or its own (an app), else the default."""
        profile_id = await self.assigned(org_id, producer)
        profile = (await self._db.get(org_id, profile_id) if profile_id else None) or await self.default(org_id)
        effective = VmCaps.clamped(self.settings_of(profile), await self._caps.get())
        return ProducerVmProfile(
            **self.served(profile, effective).model_dump(),
            volume_gib=await VolumeSizes.effective(org_id, producer, effective),
        )


class VmQuotas:
    """An organisation's quota of awake VM time. Stored; not enforced yet."""

    async def get(self, org_id: str) -> OrgVmQuotaResponse:
        row = await OrgVmQuotaDB().get(org_id)
        return OrgVmQuotaResponse(
            org_id=org_id,
            awake_vm_seconds_per_month=row.awake_vm_seconds_per_month if row else None,
            updated_at=row.updated_at if row else None,
        )

    async def set(self, org_id: str, body: OrgVmQuotaBody, *, by_user_id: str) -> OrgVmQuotaResponse:
        await OrgVmQuotaDB().put(
            OrgVmQuota(
                org_id=org_id,
                awake_vm_seconds_per_month=body.awake_vm_seconds_per_month,
                updated_by_user_id=by_user_id,
            )
        )
        return await self.get(org_id)
