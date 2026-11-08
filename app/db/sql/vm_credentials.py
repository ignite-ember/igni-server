"""Producers' VM credentials, in the organisation's own database."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import StrEnum

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.db.sql.bind_chunks import chunks
from app.db.sql.models.vm_credential import VmCredential
from app.db.sql.schemas.vm_credential import PendingPickup
from app.db.sql.tenancy import TenantData

#: How many rotated-away secrets are remembered for reuse detection.
REMEMBERED = 20

#: How long after a rotation the credential it replaced may be presented
#: once more — a VM whose reply was lost retrying.
RETRY_GRACE = timedelta(seconds=60)


class Rotation(StrEnum):
    """What presenting a credential came to."""

    ROTATED = 'rotated'
    #: The one retry of the last rotation, within the grace: the same
    #: successor again.
    RETRIED = 'retried'
    #: An older generation of a working family: it has been revoked.
    REUSED = 'reused'
    #: Not this family's, or a generation too old to recognise.
    UNKNOWN = 'unknown'
    REVOKED = 'revoked'


class VmCredentialsDB(TenantData):
    async def issue(self, org_id: str, credential: VmCredential, *, by_id: str, replaced_reason: str) -> VmCredential:
        """Store a new family, revoking the producer's working one in the same transaction."""
        now = datetime.now(timezone.utc)
        for attempt in range(2):
            try:
                async with self.session_scope(org_id) as session:
                    await session.execute(
                        update(VmCredential)
                        .where(
                            VmCredential.producer_kind == credential.producer_kind,
                            VmCredential.producer_id == credential.producer_id,
                            VmCredential.revoked_at.is_(None),
                        )
                        .values(revoked_at=now, revoked_reason=replaced_reason, revoked_by_id=by_id)
                    )
                    session.add(credential)
                return credential
            except IntegrityError:
                # Two issues for one producer at once: the other won the
                # unique index; this one replaces it on the second try.
                if attempt:
                    raise
        return credential

    async def get(self, org_id: str, credential_id: str) -> VmCredential | None:
        async with self.session_scope(org_id) as session:
            return await session.get(VmCredential, credential_id)

    async def active(
        self, org_id: str, *, producer_kind: str | None = None, producer_id: str | None = None
    ) -> list[VmCredential]:
        query = select(VmCredential).where(VmCredential.revoked_at.is_(None))
        if producer_kind:
            query = query.where(VmCredential.producer_kind == producer_kind)
        if producer_id:
            query = query.where(VmCredential.producer_id == producer_id)
        async with self.session_scope(org_id) as session:
            rows = await session.execute(query.order_by(VmCredential.created_at))
            return list(rows.scalars().all())

    async def rotate(
        self,
        org_id: str,
        credential_id: str,
        *,
        generation: int,
        presented_hash: str,
        successor_hashes: list[str],
    ) -> Rotation:
        """Swap the presented secret for its successor — once — or recognise one retry of that swap.

        `successor_hashes` are the hashes of the successor derived from the
        presented secret under each key this deployment holds, current
        first: the current one becomes the new secret; any of them equal to
        the stored secret proves a retry derives what was issued.
        """
        now = datetime.now(timezone.utc)
        async with self.session_scope(org_id) as session:
            row = (
                await session.execute(select(VmCredential).where(VmCredential.id == credential_id).with_for_update())
            ).scalar_one_or_none()
            if row is None:
                return Rotation.UNKNOWN
            if row.revoked_at is not None:
                return Rotation.REVOKED
            if generation == row.generation and presented_hash == row.secret_hash:
                row.previous_hashes = [[row.generation, row.secret_hash], *row.previous_hashes][:REMEMBERED]
                row.generation += 1
                row.secret_hash = successor_hashes[0]
                row.last_used_at = now
                row.rotated_at = now
                row.retry_used = False
                return Rotation.ROTATED
            just_before = row.previous_hashes[0] if row.previous_hashes else None
            if (
                just_before is not None
                and [generation, presented_hash] == list(just_before)
                and generation == row.generation - 1
                and not row.retry_used
                and row.rotated_at is not None
                and now - row.rotated_at <= RETRY_GRACE
                and row.secret_hash in successor_hashes
            ):
                row.retry_used = True
                row.last_used_at = now
                return Rotation.RETRIED
            if [generation, presented_hash] in [list(pair) for pair in row.previous_hashes]:
                row.revoked_at = now
                row.revoked_reason = 'reused'
                return Rotation.REUSED
            return Rotation.UNKNOWN

    async def revoke(self, org_id: str, credential_id: str, *, reason: str, by_id: str | None) -> bool:
        async with self.session_scope(org_id) as session:
            result = await session.execute(
                update(VmCredential)
                .where(VmCredential.id == credential_id, VmCredential.revoked_at.is_(None))
                .values(revoked_at=datetime.now(timezone.utc), revoked_reason=reason, revoked_by_id=by_id)
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def revoke_producer(self, org_id: str, producer_kind: str, producer_id: str, *, reason: str) -> int:
        async with self.session_scope(org_id) as session:
            result = await session.execute(
                update(VmCredential)
                .where(
                    VmCredential.producer_kind == producer_kind,
                    VmCredential.producer_id == producer_id,
                    VmCredential.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(timezone.utc), revoked_reason=reason)
            )
            return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def revoke_producers(self, org_id: str, producer_kind: str, producer_ids: list[str], *, reason: str) -> int:
        """Revoke the working credentials of many producers of one kind at once."""
        revoked = 0
        async with self.session_scope(org_id) as session:
            for part in chunks(producer_ids):
                result = await session.execute(
                    update(VmCredential)
                    .where(
                        VmCredential.producer_kind == producer_kind,
                        VmCredential.producer_id.in_(part),
                        VmCredential.revoked_at.is_(None),
                    )
                    .values(revoked_at=datetime.now(timezone.utc), revoked_reason=reason)
                )
                revoked += int(result.rowcount or 0)  # type: ignore[attr-defined]
        return revoked

    async def pick_up(
        self, org_id: str, producer_kind: str, producer_id: str, *, by_client_id: str
    ) -> PendingPickup | None:
        """An app's or a space's credential, once, still encrypted; None ever after."""
        async with self.session_scope(org_id) as session:
            row = (
                await session.execute(
                    select(VmCredential)
                    .where(
                        VmCredential.producer_kind == producer_kind,
                        VmCredential.producer_id == producer_id,
                        VmCredential.revoked_at.is_(None),
                        VmCredential.pending_pickup.is_not(None),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None or row.pending_pickup is None:
                return None
            sealed = row.pending_pickup
            row.pending_pickup = None
            row.picked_up_at = datetime.now(timezone.utc)
            row.picked_up_by = by_client_id
            return PendingPickup(family_id=row.id, sealed=sealed)
