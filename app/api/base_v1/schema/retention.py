"""Pydantic schemas for removed members' retention, legal holds and exports (`docs/service-identity.md` §18)."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.security.producer import ProducerRef

#: The longest anything may name, in days — the platform's maximum sets the real limit.
MAX_RETENTION_DAYS = 3650


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


class LegalHoldList(BaseModel):
    holds: list[LegalHoldResponse]


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
