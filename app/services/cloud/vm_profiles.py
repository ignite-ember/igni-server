"""VM profiles: what each producer's microVM gets.

An org admin defines named profiles — memory (the most a VM may have,
and what it normally has), vCPUs, how long idle before a pause (or never),
how long an app's unwoken VM is kept before it is erased, and the
packages built into the profile's read-only layer — and assigns one to
each group and each app. A person gets their one group's; a group or app
with none gets the organisation's default profile. A super admin sets
the platform's caps (`VmCaps`), which every profile must fit, and may
define and assign profiles in any organisation
(`docs/service-identity.md` §12).

Refusals are the typed errors of `vm_profile_errors`;
`VmProfileRefusals` turns them into HTTP answers.
"""

from __future__ import annotations

import uuid

from app.api.base_v1.schema.effective_vm_profile import EffectiveVmProfile, VmPackages
from app.api.base_v1.schema.vm_profiles import (
    PlatformVmCapsBody,
    ProducerAssignment,
    ProducerVmProfile,
    VmProfileResponse,
    VmProfileSettings,
    VmProfilesOverview,
)
from app.db.sql.models.app_space import SPACE
from app.db.sql.models.registered_client import APP
from app.db.sql.models.vm_profile import VmProfile
from app.db.sql.registered_clients import RegisteredClientDB
from app.db.sql.spaces import SpacesDB
from app.db.sql.team import TeamDB
from app.db.sql.vm_profiles import VmProfileNameTaken, VmProfilesDB
from app.security.producer import ProducerRef
from app.services.cloud.vm_caps import VmCaps
from app.services.cloud.vm_defaults import VmDefaults
from app.services.cloud.vm_profile_errors import (
    AppNotFound,
    AppNotInOrg,
    DefaultProfileKept,
    GroupNotFound,
    MemberNotInOrg,
    ProfileBeyondCaps,
    ProfileNameTaken,
    ProfileNotFound,
    SpaceNotInOrg,
)
from app.services.cloud.vm_profile_versions import ProfileVersion
from app.services.cloud.vm_quotas import VmQuotas
from app.services.cloud.volume_sizes import VolumeSizes
from app.services.portal.group_extensions import GroupExtensions


class VmProfiles:
    """One organisation's profiles, their assignment, and the profile a producer gets."""

    def __init__(
        self,
        *,
        db: VmProfilesDB | None = None,
        caps: VmCaps | None = None,
        quotas: VmQuotas | None = None,
        clients: RegisteredClientDB | None = None,
        spaces: SpacesDB | None = None,
        team: TeamDB | None = None,
        groups: GroupExtensions | None = None,
    ) -> None:
        self._db = db or VmProfilesDB()
        self._caps = caps or VmCaps()
        self._quotas = quotas or VmQuotas()
        self._clients = clients or RegisteredClientDB()
        self._spaces = spaces or SpacesDB()
        self._team = team or TeamDB()
        self._groups = groups or GroupExtensions()

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
            internet=profile.internet,
        )

    @staticmethod
    def _row(settings: VmProfileSettings, **extra: object) -> VmProfile:
        """A profile row holding *settings*, not yet stored."""
        return VmProfile(**settings.model_dump(exclude={'packages'}), packages=settings.packages.model_dump(), **extra)

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
            internet=effective.internet,
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
        return await self._db.default(org_id, self._row(VmDefaults.standard(), id=str(uuid.uuid4())))

    async def _profile(self, org_id: str, profile_id: str) -> VmProfile:
        found = await self._db.get(org_id, profile_id)
        if found is None:
            raise ProfileNotFound()
        return found

    async def _fits(self, settings: VmProfileSettings) -> None:
        reason = VmCaps.refusal(settings, await self._caps.get())
        if reason is not None:
            raise ProfileBeyondCaps(reason)

    async def overview(self, org_id: str) -> VmProfilesOverview:
        default = await self.default(org_id)
        caps = await self._caps.get()
        clients = {client.client_id: client.name for client in await self._clients.apps_of(org_id)}
        return VmProfilesOverview(
            org_id=org_id,
            caps=caps,
            quota=await self._quotas.get(org_id),
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
                org_id, self._row(settings, created_by_user_id=by_user_id, updated_by_user_id=by_user_id)
            )
        except VmProfileNameTaken as exc:
            raise ProfileNameTaken(settings.name) from exc
        return self.response(profile, await self._caps.get())

    async def replace(
        self, org_id: str, profile_id: str, settings: VmProfileSettings, *, by_user_id: str
    ) -> VmProfileResponse:
        await self._profile(org_id, profile_id)
        await self._fits(settings)
        try:
            profile = await self._db.replace(org_id, self._row(settings, id=profile_id, updated_by_user_id=by_user_id))
        except VmProfileNameTaken as exc:
            raise ProfileNameTaken(settings.name) from exc
        return self.response(profile, await self._caps.get())

    async def delete(self, org_id: str, profile_id: str) -> None:
        profile = await self._profile(org_id, profile_id)
        if profile.is_default:
            raise DefaultProfileKept()
        await self._db.remove(org_id, profile_id)

    async def _assignable(self, org_id: str, profile_id: str | None) -> None:
        if profile_id is not None:
            await self._profile(org_id, profile_id)

    async def assign_group(self, org_id: str, group_id: str, profile_id: str | None) -> None:
        await self._assignable(org_id, profile_id)
        if not await self._db.assign_group(org_id, group_id, profile_id):
            raise GroupNotFound()

    async def assign_app(self, org_id: str, app_id: str, profile_id: str | None) -> None:
        await self._assignable(org_id, profile_id)
        if not await self._db.assign_app(org_id, app_id, profile_id):
            raise AppNotFound()

    async def assigned(self, org_id: str, producer: ProducerRef) -> str | None:
        """The profile id assigned to the producer's group or app; None for the default.

        Raises `ProducerNotInOrg` for no such producer.
        """
        if producer.kind == SPACE:
            # A space's VM gets its app's profile.
            space = await self._spaces.get(org_id, producer.id)
            if space is None:
                raise SpaceNotInOrg()
            producer = ProducerRef(kind=APP, id=space.app_id)
        if producer.kind == APP:
            exists, profile_id = await self._db.of_app(org_id, producer.id)
            if not exists:
                raise AppNotInOrg()
            return profile_id
        if await self._team.get_active_member(org_id, producer.id) is None:
            raise MemberNotInOrg()
        group = await self._groups.group_of(producer.id, org_id)
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
