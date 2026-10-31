"""Pydantic schemas for the gateway's feed of producers (`docs/service-identity.md` §11.3)."""

from datetime import datetime

from pydantic import BaseModel

from app.api.base_v1.schema.effective_vm_profile import EffectiveVmProfile
from app.security.producer import ProducerRef


class ProducerFeedItem(BaseModel):
    """One producer whose state changed: what it is now."""

    org_id: str
    producer: ProducerRef
    #: Which life of the producer, and so which VM: a person's membership
    #: (new each time they are added to the organisation), an app's or a
    #: space's own id. Key the VM by producer and incarnation; a new one
    #: is a fresh VM, and the old incarnation's row says `removed`.
    incarnation: str
    #: A person: an active member, not deactivated. An app: registered,
    #: with the cloud enabled. A space: declared, its app registered.
    active: bool
    #: Removed by its owner — a space or an app deleted, an account
    #: deleted or erased, an organisation deleted: erase the VM's volume
    #: and keys. Inactive and not removed is only a suspended VM. Once
    #: true, never false again.
    removed: bool
    #: When to erase a removed producer's VM state, volume and keys; null
    #: while not scheduled — not removed, or under a legal hold. Until
    #: then a removed producer's VM is only suspended.
    erase_at: datetime | None = None
    #: The `version` and `packages_version` of the producer's VM profile
    #: (§12); the last ones it had once it is no longer active.
    profile_version: str
    packages_version: str
    #: The profile itself, as the producer's VM gets it (§12): the gateway
    #: keeps it with the producer and wakes the VM from it, never asking
    #: igni server on a wake. Equal exactly when `profile_version` is.
    #: Null once removed.
    profile: EffectiveVmProfile | None
    #: The producer's volume in GiB (§12.1). It only ever grows: a larger
    #: one is the gateway's cue to grow the volume.
    volume_gib: int
    #: Who owns the producer: a space's app (`{"kind": "app", "id"}`), so
    #: the node provisions it with its owner; null for a person or an app.
    owner: ProducerRef | None = None
    #: When igni server saw the change.
    changed_at: datetime


class ProducerFeedPage(BaseModel):
    producers: list[ProducerFeedItem]
    #: Opaque: pass it back as `since`. Unchanged when nothing is new.
    cursor: str


class RevocationItem(BaseModel):
    """Stop accepting tokens for this subject issued at or before `not_before_ms` (`iat_ms <= not_before_ms`)."""

    #: `person`, `app`, `space` or `org`.
    kind: str
    #: Null for a person: in every organisation.
    org_id: str | None
    subject_id: str
    #: The person's membership revoked (§11.3), when it is one.
    incarnation: str | None
    #: Unix milliseconds, compared with a token's `iat_ms`.
    not_before_ms: int
    #: `removed`, `inactive`, `erased`, `sessions_withdrawn`.
    reason: str


class RevocationPage(BaseModel):
    revocations: list[RevocationItem]
    #: Opaque: pass it back as `since`. Unchanged when nothing is new.
    cursor: str
