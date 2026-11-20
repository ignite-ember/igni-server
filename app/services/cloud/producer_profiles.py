"""One organisation's VM profiles and groups, read once, applied to any of its producers."""

from __future__ import annotations

from app.api.base_v1.schema.vm_profiles import PlatformVmCapsBody
from app.db.sql.models.org_group import LOCAL_AND_CLOUD
from app.db.sql.models.producer_volume import ProducerVolumeSize
from app.db.sql.models.vm_profile import VmProfile
from app.db.sql.schemas.producer_feed import ProducerKey, ProducerState
from app.db.sql.vm_profiles import VmProfilesDB
from app.services.cloud.vm_caps import VmCaps
from app.services.cloud.vm_profile_versions import ProfileVersion
from app.services.cloud.vm_profiles import VmProfiles
from app.services.cloud.volume_sizes import VolumeSizes


class ProducerProfiles:
    """An organisation's profiles, which group gets which, where each runs igni, and the state they give a producer."""

    def __init__(
        self,
        *,
        caps: PlatformVmCapsBody,
        default: VmProfile,
        profiles: dict[str, VmProfile],
        group_profiles: dict[str, str | None],
        default_group_profile: str | None,
        group_modes: dict[str, str] | None = None,
        default_group_mode: str = LOCAL_AND_CLOUD,
    ) -> None:
        self._caps = caps
        self._default = default
        self._profiles = profiles
        self._group_profiles = group_profiles
        self._default_group_profile = default_group_profile
        self._group_modes = group_modes or {}
        self._default_group_mode = default_group_mode

    @classmethod
    async def load(
        cls, org_id: str, *, caps: PlatformVmCapsBody, vm_profiles: VmProfiles, db: VmProfilesDB
    ) -> ProducerProfiles:
        """Read the organisation's profiles and groups once."""
        default = await vm_profiles.default(org_id)
        profiles = {profile.id: profile for profile in await db.all(org_id)}
        group_profiles: dict[str, str | None] = {}
        group_modes: dict[str, str] = {}
        default_group_profile: str | None = None
        default_group_mode = LOCAL_AND_CLOUD
        for group in await db.groups(org_id):
            group_profiles[group.id] = group.vm_profile_id
            group_modes[group.id] = group.mode
            if group.is_default:
                default_group_profile = group.vm_profile_id
                default_group_mode = group.mode
        return cls(
            caps=caps,
            default=default,
            profiles=profiles,
            group_profiles=group_profiles,
            default_group_profile=default_group_profile,
            group_modes=group_modes,
            default_group_mode=default_group_mode,
        )

    def of_group(self, group: str | None) -> str | None:
        """The profile a person in *group* gets; in no group yet, the Default group's."""
        return self._group_profiles.get(group) if group is not None else self._default_group_profile

    def mode_of(self, group: str | None) -> str:
        """Where a person in *group* gets igni; in no group yet, where the Default group's people do."""
        if group is None:
            return self._default_group_mode
        return self._group_modes.get(group, LOCAL_AND_CLOUD)

    def state(
        self,
        key: ProducerKey,
        *,
        profile_id: str | None,
        active: bool,
        inactive_reason: str | None = None,
        owner: str | None = None,
        incarnation: str | None = None,
        grown: ProducerVolumeSize | None = None,
        legal_hold: bool = False,
    ) -> ProducerState:
        """What the producer *key* is now, with *profile_id* (or the default) clamped to the caps."""
        profile = self._profiles.get(profile_id or '') or self._default
        effective = VmCaps.clamped(VmProfiles.settings_of(profile), self._caps)
        incarnation = incarnation or key.producer_id
        return ProducerState(
            key=key,
            active=active,
            inactive_reason=None if active else inactive_reason,
            profile_version=ProfileVersion.of(profile.id, effective),
            packages_version=ProfileVersion.packages(effective),
            profile=VmProfiles.served(profile, effective),
            owner_app_id=owner,
            incarnation=incarnation,
            volume_gib=VolumeSizes.of(effective, grown, incarnation),
            legal_hold=legal_hold,
        )
