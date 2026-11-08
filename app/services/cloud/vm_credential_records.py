"""VM credentials as igni server hands them out: never the secret, except the once it must be."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel

from app.db.sql.models.vm_credential import VmCredential
from app.security.producer import ProducerRef

#: A family unused this long stops working.
IDLE_LIMIT = timedelta(days=90)


class VmCredentialRecord(BaseModel):
    """A family as the portal shows it: never the secret."""

    credential_id: str
    org_id: str
    producer: ProducerRef
    created_at: datetime
    last_used_at: datetime | None
    #: When it stops working if nobody uses it before then.
    idle_expires_at: datetime
    #: An app's, issued and not yet picked up by the node agent.
    awaiting_pickup: bool
    picked_up_at: datetime | None
    generation: int

    @classmethod
    def of(cls, org_id: str, row: VmCredential) -> VmCredentialRecord:
        return cls(
            credential_id=row.id,
            org_id=org_id,
            producer=ProducerRef.stored(row.producer_kind, row.producer_id),
            created_at=row.created_at,
            last_used_at=row.last_used_at,
            idle_expires_at=(row.last_used_at or row.created_at) + IDLE_LIMIT,
            awaiting_pickup=row.pending_pickup is not None,
            picked_up_at=row.picked_up_at,
            generation=row.generation,
        )

    def idle_at(self, now: datetime) -> bool:
        """Unused for longer than the limit by *now*."""
        return self.idle_expires_at < now


class VmCredentialList(BaseModel):
    credentials: list[VmCredentialRecord]


class IssuedVmCredential(BaseModel):
    """A new family, and its first credential — the one time igni server holds it in the clear."""

    record: VmCredentialRecord
    vm_credential: str


class PickedUp(BaseModel):
    credential_id: str
    vm_credential: str
