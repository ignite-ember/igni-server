"""The deployment's VM defaults: the caps and the Standard profile until somebody sets their own."""

from __future__ import annotations

from app.api.base_v1.schema.vm_profiles import PlatformVmCapsBody, VmProfileSettings
from app.config import settings


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
