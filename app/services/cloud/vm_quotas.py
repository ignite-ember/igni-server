"""An organisation's quota of awake VM time, set by a super admin."""

from __future__ import annotations

from app.api.base_v1.schema.vm_profiles import OrgVmQuotaBody, OrgVmQuotaResponse
from app.db.sql.models.org_vm_quota import OrgVmQuota
from app.db.sql.vm_profiles import OrgVmQuotaDB


class VmQuotas:
    """An organisation's quota of awake VM time: kept and shown to its admins; nothing here enforces it."""

    def __init__(self, *, db: OrgVmQuotaDB | None = None) -> None:
        self._db = db or OrgVmQuotaDB()

    async def get(self, org_id: str) -> OrgVmQuotaResponse:
        row = await self._db.get(org_id)
        return OrgVmQuotaResponse(
            org_id=org_id,
            awake_vm_seconds_per_month=row.awake_vm_seconds_per_month if row else None,
            updated_at=row.updated_at if row else None,
        )

    async def set(self, org_id: str, body: OrgVmQuotaBody, *, by_user_id: str) -> OrgVmQuotaResponse:
        await self._db.put(
            OrgVmQuota(
                org_id=org_id,
                awake_vm_seconds_per_month=body.awake_vm_seconds_per_month,
                updated_by_user_id=by_user_id,
            )
        )
        return await self.get(org_id)
