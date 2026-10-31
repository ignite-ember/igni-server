"""Shapes the producer feed's store takes and returns."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.api.base_v1.schema.effective_vm_profile import EffectiveVmProfile


class ProducerKey(BaseModel, frozen=True):
    """One producer of one organisation."""

    org_id: str
    producer_kind: str
    producer_id: str


class ProducerState(BaseModel):
    """What the feed reports about a producer, as igni server computes it now."""

    key: ProducerKey
    active: bool
    profile_version: str
    packages_version: str
    #: What its VM gets: equal exactly when `profile_version` is.
    profile: EffectiveVmProfile
    #: Its volume in GiB: the profile's, or more where an admin grew it.
    volume_gib: int
    #: A space's app; None for a person or an app.
    owner_app_id: str | None = None
    #: Which life of the producer this is: a person's membership in the
    #: organisation (a new one each time they are added), an app's or a
    #: space's own id. A new incarnation is a new VM.
    incarnation: str


class FeedMark(BaseModel, frozen=True):
    """A mark a trigger wrote: which producers may have changed (`app.db.sql.models.producer_feed`)."""

    org_id: str
    producer_kind: str
    producer_id: str
    #: How many marks it stands for; the feed forgets it only if still this many.
    n: int


class Census(BaseModel):
    """What a set of producers of one kind are now.

    `states` holds those that exist, active or not; `removed` those their
    owner removed for good. One in neither is inactive and kept — a
    person no longer a member — and its VM is only suspended.
    """

    states: list[ProducerState] = Field(default_factory=list)
    removed: set[str] = Field(default_factory=set)
    #: Days from removal to erasure, per removed id; absent is 0 (at once).
    erase_after_days: dict[str, int] = Field(default_factory=dict)
    #: Removed ids under a legal hold: no erasure is scheduled.
    held: set[str] = Field(default_factory=set)


class Member(BaseModel):
    """A person's membership, as the feed needs it."""

    active: bool
    #: The membership's id: a person removed and added again has another.
    incarnation: str


class Scheduled(BaseModel):
    """An erasure the feed scheduled, moved or stopped: what the audit trail records."""

    org_id: str
    producer_kind: str
    producer_id: str
    incarnation: str
    #: Null: held.
    erase_at: datetime | None
    #: `removed`, `held`, `released`, `erased_on_request`.
    why: str


class RevocationEntry(BaseModel):
    """Tokens for a subject issued at or before `not_before_ms` (Unix milliseconds) stop being accepted."""

    kind: str
    #: Null with kind `person`: in every organisation.
    org_id: str | None
    subject_id: str
    incarnation: str | None = None
    not_before_ms: int
    reason: str
