"""Pydantic schemas for removed members' retention, legal holds and exports (`docs/service-identity.md` §18)."""

from datetime import datetime
import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.security.producer import ProducerRef

#: The longest anything may name, in days — the platform's maximum sets the real limit.
MAX_RETENTION_DAYS = 3650

#: The largest erasure summary kept in the audit trail, in characters of JSON.
MAX_SUMMARY = 8192


class RetentionCapsBody(BaseModel):
    #: The longest an organisation may keep a removed member's VM.
    max_removed_member_retention_days: int = Field(ge=0, le=MAX_RETENTION_DAYS)


class RetentionCapsResponse(RetentionCapsBody):
    updated_at: datetime | None = None


class OrgRetentionBody(BaseModel):
    #: Days a removed member's VM is kept before it is erased; 0 erases at once.
    removed_member_retention_days: int = Field(ge=0, le=MAX_RETENTION_DAYS)


class OrgRetentionResponse(OrgRetentionBody):
    org_id: str
    #: The platform's maximum.
    max_removed_member_retention_days: int
    updated_at: datetime | None = None


class LegalHoldBody(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class HeldIncarnation(BaseModel):
    """One removed incarnation of the person, and when it is erased (null while held)."""

    incarnation: str
    removed_at: datetime | None
    erase_at: datetime | None


class LegalHoldResponse(BaseModel):
    org_id: str
    user_id: str
    reason: str
    placed_by_user_id: str
    placed_at: datetime
    #: Their removed incarnations in this organisation not yet erased.
    incarnations: list[HeldIncarnation]


class AppLegalHoldResponse(BaseModel):
    """A hold on an app: it and its spaces are not erased while it stands, discarded or not."""

    org_id: str
    app_id: str
    reason: str
    placed_by_user_id: str
    placed_at: datetime


class LegalHoldList(BaseModel):
    holds: list[LegalHoldResponse]
    #: Holds on apps (§18.2).
    apps: list[AppLegalHoldResponse] = Field(default_factory=list)


class DiscardRequest(BaseModel):
    #: Which removed incarnation; every one not yet erased when left out.
    incarnation: str | None = None


class ErasureRecord(BaseModel):
    """A removed or discarded producer's VM and where its erasure stands (§18.5)."""

    producer: ProducerRef
    #: The person's email, or the app's name when it was discarded; null when unknown.
    name: str | None
    incarnation: str
    removed_at: datetime | None
    erase_at: datetime | None
    #: `scheduled` (erased at `erase_at`), `held` (a legal hold: nothing is
    #: erased), `erasing` (due: the gateway is erasing it), `erased`.
    state: Literal['scheduled', 'held', 'erasing', 'erased']
    #: When the gateway reported the erasure done.
    erased_at: datetime | None
    legal_hold: bool
    #: Exports of this incarnation requested, being made or ready (§18.4):
    #: discarding now ends the chance to take another.
    exports: int
    discarded_by_user_id: str | None
    discarded_at: datetime | None


class ErasureList(BaseModel):
    org_id: str
    erasures: list[ErasureRecord]


class DiscardedMember(BaseModel):
    erasures: list[ErasureRecord]


class ErasureDoneBody(BaseModel):
    """The gateway's report: a removed incarnation's VM is erased (§18.3)."""

    incarnation: str = Field(min_length=1, max_length=200)
    #: What the node answered, part by part.
    summary: dict[str, Any] = Field(default_factory=dict)

    @field_validator('summary')
    @classmethod
    def _bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, default=str)) > MAX_SUMMARY:
            raise ValueError(f'summary is limited to {MAX_SUMMARY} characters of JSON')
        return value


class ExportRequest(BaseModel):
    #: Which removed incarnation; the latest one when left out.
    incarnation: str | None = None


class ExportRecord(BaseModel):
    export_id: str
    org_id: str
    producer: ProducerRef
    incarnation: str
    #: `requested` → `handed_out` → `ready`, or `failed`, then `expired`.
    state: str
    requested_by_user_id: str
    requested_at: datetime
    handed_out_at: datetime | None
    finished_at: datetime | None
    #: Bytes, once ready.
    size: int | None
    error: str | None
    #: The archive is deleted then.
    expires_at: datetime | None


class ExportList(BaseModel):
    exports: list[ExportRecord]


class PendingExport(BaseModel):
    """What the node agent runs, against the organisation's igni database."""

    export_id: str
    org_id: str
    producer: ProducerRef
    incarnation: str
    #: `igni-cloud db export-producer --org <org_id> --producer person:<id> --incarnation <id>`;
    #: the node agent appends `--out <file>`.
    command: list[str]


class PendingExports(BaseModel):
    exports: list[PendingExport]


class ExportFailed(BaseModel):
    error: str = Field(min_length=1, max_length=4000)
