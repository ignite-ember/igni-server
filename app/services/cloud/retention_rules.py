"""How long a removed member's VM is kept, and the legal holds that stop its erasure.

The organisation is the controller of its members' work. When a person
is removed from it, their producer there is removed at once — never
usable again — but its VM's state, volume and keys are erased only after
the organisation's `removed_member_retention_days` (default 30; 0 is at
once), bounded by the platform's maximum. A legal hold on the person
stops that clock for every removed incarnation of theirs in the
organisation until it is released. Spaces, apps, accounts and
organisations deleted, and a person erased on request, are erased at
once (`docs/service-identity.md` §18).
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.api.base_v1.schema.retention import (
    OrgRetentionBody,
    OrgRetentionResponse,
    RetentionCapsBody,
    RetentionCapsResponse,
)
from app.db.sql.models.retention import (
    DEFAULT_MAX_REMOVED_MEMBER_RETENTION_DAYS,
    DEFAULT_REMOVED_MEMBER_RETENTION_DAYS,
    OrgRetention,
    PlatformRetention,
)
from app.db.sql.retention import RetentionDB


class Retention:
    def __init__(self) -> None:
        self._db = RetentionDB()

    async def caps(self) -> RetentionCapsResponse:
        row = await self._db.platform()
        if row is None:
            return RetentionCapsResponse(max_removed_member_retention_days=DEFAULT_MAX_REMOVED_MEMBER_RETENTION_DAYS)
        return RetentionCapsResponse(
            max_removed_member_retention_days=row.max_removed_member_retention_days, updated_at=row.updated_at
        )

    async def set_caps(self, body: RetentionCapsBody, *, by_user_id: str) -> RetentionCapsResponse:
        await self._db.put_platform(PlatformRetention(**body.model_dump(), updated_by_user_id=by_user_id))
        return await self.caps()

    async def of_org(self, org_id: str) -> OrgRetentionResponse:
        cap = (await self.caps()).max_removed_member_retention_days
        row = await self._db.org(org_id)
        return OrgRetentionResponse(
            org_id=org_id,
            removed_member_retention_days=min(
                row.removed_member_retention_days if row else DEFAULT_REMOVED_MEMBER_RETENTION_DAYS, cap
            ),
            max_removed_member_retention_days=cap,
            updated_at=row.updated_at if row else None,
        )

    async def set_org(self, org_id: str, body: OrgRetentionBody, *, by_user_id: str) -> OrgRetentionResponse:
        cap = (await self.caps()).max_removed_member_retention_days
        if body.removed_member_retention_days > cap:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'This platform keeps a removed member for at most {cap} days',
            )
        await self._db.put_org(OrgRetention(org_id=org_id, **body.model_dump(), updated_by_user_id=by_user_id))
        return await self.of_org(org_id)

    async def removed_member_days(self, org_id: str) -> int:
        """Days between a member's removal and their VM's erasure, as things stand now."""
        return (await self.of_org(org_id)).removed_member_retention_days
