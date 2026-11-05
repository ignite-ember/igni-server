"""VM profile requests, in the organisation's own database (`docs/service-identity.md` §12.3)."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timezone
import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.db.sql.models.vm_profile_request import PENDING, VmProfileRequest
from app.db.sql.tenancy import TenantData


class VmProfileRequestsDB(TenantData):
    """One request per app and per extension, and its decision."""

    async def all(self, org_id: str) -> list[VmProfileRequest]:
        async with self.session_scope(org_id) as session:
            rows = await session.execute(select(VmProfileRequest).order_by(VmProfileRequest.requested_at.desc()))
            return list(rows.scalars().all())

    async def get(self, org_id: str, request_id: str) -> VmProfileRequest | None:
        async with self.session_scope(org_id) as session:
            return await session.get(VmProfileRequest, request_id)

    async def of(self, org_id: str, subject_kind: str, subject_id: str) -> VmProfileRequest | None:
        async with self.session_scope(org_id) as session:
            rows = await session.execute(
                select(VmProfileRequest).where(
                    VmProfileRequest.subject_kind == subject_kind, VmProfileRequest.subject_id == subject_id
                )
            )
            return rows.scalars().first()

    async def ask(
        self,
        org_id: str,
        *,
        subject_kind: str,
        subject_id: str,
        settings: dict[str, object],
        version: str,
        requested_by_kind: str,
        requested_by_id: str,
    ) -> tuple[VmProfileRequest, bool]:
        """Keep what the subject asks for now; whether that is new (pending again) or the same as before.

        The same version again changes nothing — a pending request stays
        pending, a decided one stays decided. Another version replaces it,
        pending, its decision cleared; the profile an earlier approval
        made keeps what was approved then.
        """
        async with self.session_scope(org_id) as session:
            made = await session.execute(
                insert(VmProfileRequest)
                .values(
                    id=str(uuid.uuid4()),
                    subject_kind=subject_kind,
                    subject_id=subject_id,
                    settings=settings,
                    version=version,
                    state=PENDING,
                    requested_by_kind=requested_by_kind,
                    requested_by_id=requested_by_id,
                    requested_at=datetime.now(timezone.utc),
                )
                .on_conflict_do_nothing(index_elements=['subject_kind', 'subject_id'])
                .returning(VmProfileRequest.id)
            )
            new = made.scalar_one_or_none() is not None
            row = (
                (
                    await session.execute(
                        select(VmProfileRequest)
                        .where(VmProfileRequest.subject_kind == subject_kind, VmProfileRequest.subject_id == subject_id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .one()
            )
            if new or row.version == version:
                return row, new
            row.settings = settings
            row.version = version
            row.state = PENDING
            row.requested_by_kind = requested_by_kind
            row.requested_by_id = requested_by_id
            row.requested_at = datetime.now(timezone.utc)
            row.decided_by_user_id = None
            row.decided_as = None
            row.decided_at = None
            row.reason = None
            return row, True

    async def decide(
        self,
        org_id: str,
        request_id: str,
        *,
        state: str,
        by_user_id: str,
        acting_as: str,
        reason: str | None = None,
        profile_id: str | None = None,
    ) -> VmProfileRequest | None:
        async with self.session_scope(org_id) as session:
            row = await session.get(VmProfileRequest, request_id)
            if row is None:
                return None
            row.state = state
            row.decided_by_user_id = by_user_id
            row.decided_as = acting_as
            row.decided_at = datetime.now(timezone.utc)
            row.reason = reason
            if profile_id is not None:
                row.profile_id = profile_id
            return row

    async def remove(self, org_id: str, subject_kind: str, subject_id: str) -> None:
        async with self.session_scope(org_id) as session:
            await session.execute(
                delete(VmProfileRequest).where(
                    VmProfileRequest.subject_kind == subject_kind, VmProfileRequest.subject_id == subject_id
                )
            )

    async def keep_only(self, org_id: str, subject_kind: str, subject_ids: Collection[str]) -> None:
        """Forget every request of this kind but those of `subject_ids` (an extension that asks no more)."""
        async with self.session_scope(org_id) as session:
            await session.execute(
                delete(VmProfileRequest).where(
                    VmProfileRequest.subject_kind == subject_kind, VmProfileRequest.subject_id.not_in(subject_ids)
                )
            )
