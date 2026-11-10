"""The platform's caps on every VM profile, set by a super admin, and fitting a profile to them."""

from __future__ import annotations

from app.api.base_v1.schema.vm_profiles import (
    PlatformVmCapsBody,
    PlatformVmCapsResponse,
    VmProfileSettings,
    VmProfileShape,
)
from app.db.sql.models.platform_vm_caps import PlatformVmCaps
from app.db.sql.vm_profiles import PlatformVmCapsDB
from app.services.cloud.vm_defaults import VmDefaults


class VmCaps:
    """The platform's caps, and fitting a profile to them."""

    def __init__(self, *, db: PlatformVmCapsDB | None = None) -> None:
        self._db = db or PlatformVmCapsDB()

    async def get(self) -> PlatformVmCapsResponse:
        row = await self._db.get()
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
        return await self._db.get() is None

    async def set(self, body: PlatformVmCapsBody, *, by_user_id: str | None) -> PlatformVmCapsResponse:
        await self._db.put(PlatformVmCaps(**body.model_dump(), updated_by_user_id=by_user_id))
        return await self.get()

    @staticmethod
    def refusal(settings: VmProfileShape, caps: PlatformVmCapsBody) -> str | None:
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
