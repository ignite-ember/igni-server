"""Pydantic schemas for VM profiles, the platform's caps and an organisation's quota.

See `docs/service-identity.md` §12.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.api.base_v1.schema.effective_vm_profile import EffectiveVmProfile, VmPackages
from app.api.base_v1.schema.vm_commands import Requester
from app.config import settings
from app.security.producer import ProducerRef

#: The largest setup script, in characters.
MAX_SETUP_SCRIPT = 65536

#: The largest volume anything may name, in GiB — the caps set the real limit.
MAX_VOLUME_GIB = 65536


class VmProfileSettings(BaseModel):
    """Everything a profile says — what an org admin writes, whole."""

    name: str = Field(min_length=1, max_length=60)
    #: The most memory the VM may have: its size at boot, which cannot
    #: change while it runs. Left out: the deployment's Standard value
    #: (`settings.cloud_vm`), as for every field below that has one.
    memory_mib: int = Field(default_factory=lambda: settings.cloud_vm.standard_memory_mib, ge=128, le=1_048_576)
    #: What the VM normally has. The node keeps the rest in the VM's
    #: balloon and gives it, up to `memory_mib`, while the VM needs it
    #: (a native build) and the node has room; it takes it back when the
    #: need is over and before the VM pauses.
    normal_memory_mib: int = Field(
        default_factory=lambda: settings.cloud_vm.standard_normal_memory_mib, ge=128, le=1_048_576
    )
    #: What the node expects the VM to use, for placing it; null: the
    #: node's own rule (a share of `memory_mib`).
    expected_memory_mib: int | None = Field(default=None, ge=128, le=1_048_576)
    vcpus: int = Field(default_factory=lambda: settings.cloud_vm.standard_vcpus, ge=1, le=256)
    #: Seconds idle before the VM is paused; null is never.
    idle_before_pause_s: int | None = Field(
        default_factory=lambda: settings.cloud_vm.standard_idle_before_pause_s, ge=0
    )
    packages: VmPackages = Field(default_factory=VmPackages)
    setup_script: str | None = Field(default=None, max_length=MAX_SETUP_SCRIPT)
    #: The volume a new VM of this profile gets, in GiB.
    volume_gib: int = Field(default_factory=lambda: settings.cloud_vm.standard_volume_gib, ge=1, le=MAX_VOLUME_GIB)
    #: How far an org admin may grow one producer's volume, in GiB.
    volume_max_gib: int = Field(
        default_factory=lambda: settings.cloud_vm.standard_volume_max_gib, ge=1, le=MAX_VOLUME_GIB
    )

    @model_validator(mode='after')
    def _volume_fits(self) -> VmProfileSettings:
        if self.volume_gib > self.volume_max_gib:
            raise ValueError('volume_gib may not exceed volume_max_gib')
        return self

    @model_validator(mode='after')
    def _normal_fits(self) -> VmProfileSettings:
        if self.normal_memory_mib > self.memory_mib:
            raise ValueError('normal_memory_mib may not exceed memory_mib')
        if self.expected_memory_mib is not None and self.expected_memory_mib > self.memory_mib:
            raise ValueError('expected_memory_mib may not exceed memory_mib')
        return self


class VmProfileResponse(VmProfileSettings):
    id: str
    is_default: bool
    #: Changes whenever anything in the profile does.
    version: str
    #: Changes whenever its packages or setup script do — the layer to rebuild.
    packages_version: str
    #: False when a cap was lowered after it was saved; it is then served clamped.
    within_caps: bool
    created_at: datetime
    updated_at: datetime


class PlatformVmCapsBody(BaseModel):
    """The super admin's limits on every profile."""

    max_memory_mib: int = Field(ge=128, le=1_048_576)
    max_vcpus: int = Field(ge=1, le=256)
    #: The longest `idle_before_pause`; null allows any, never included.
    max_idle_before_pause_s: int | None = Field(default=None, ge=0)
    #: The largest volume a profile may let a VM have (`volume_max_gib`), in GiB.
    max_volume_gib: int = Field(default=200, ge=1, le=MAX_VOLUME_GIB)


class PlatformVmCapsResponse(PlatformVmCapsBody):
    updated_at: datetime | None = None


class OrgVmQuotaBody(BaseModel):
    #: VM-seconds awake per calendar month for the whole organisation;
    #: null is unlimited. Stored, not enforced yet.
    awake_vm_seconds_per_month: int | None = Field(default=None, ge=0)


class OrgVmQuotaResponse(OrgVmQuotaBody):
    org_id: str
    enforced: bool = False
    updated_at: datetime | None = None


class AssignVmProfile(BaseModel):
    #: The profile to give; null puts it back on the organisation's default.
    profile_id: str | None = None


class ProducerAssignment(BaseModel):
    id: str
    name: str
    #: Null: the organisation's default profile.
    vm_profile_id: str | None


class VmProfilesOverview(BaseModel):
    """One organisation's profiles and who has which — the profiles page."""

    org_id: str
    caps: PlatformVmCapsResponse
    quota: OrgVmQuotaResponse
    default_profile_id: str
    profiles: list[VmProfileResponse]
    groups: list[ProducerAssignment]
    apps: list[ProducerAssignment]


class ProducerVmProfile(EffectiveVmProfile):
    """The profile a producer's VM gets, with that producer's volume (§12, §12.1)."""

    #: This producer's volume, in GiB: the profile's, or more where an
    #: org admin grew it. It never shrinks.
    volume_gib: int


class GrowVolume(BaseModel):
    #: The new size, in GiB: more than now, at most the profile's maximum.
    gib: int = Field(ge=1, le=MAX_VOLUME_GIB)


class ProducerVolume(BaseModel):
    """One producer's volume size, as an admin grew it (`docs/service-identity.md` §12.1)."""

    org_id: str
    producer: ProducerRef
    #: The life of the producer the size is for (§11.3); a new one starts afresh.
    incarnation: str
    volume_gib: int
    volume_max_gib: int
    #: Who grew it last, when; null while it has the profile's size.
    grown_by: Requester | None = None
    grown_at: datetime | None = None
