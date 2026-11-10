"""What one person authored, owned, registered or decided: `ThingsTheyCreated` and its rows.

One row model per statement in `CreatedThingsDB.things_they_created`,
its fields exactly the statement's columns in the same order (see
`SubjectRow`).

Just over 300 lines, and kept whole: it is 22 row models for the one
read, and splitting them would part rows that one statement group returns.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, JsonValue

from app.db.sql.schemas.subject_access import SubjectRow


class CreatedGroupRow(SubjectRow):
    """`org_groups` they created."""

    id: str
    org_id: str
    name: str
    created_at: datetime


class GroupEntryRow(SubjectRow):
    """`org_group_entries` they created."""

    id: str
    group_id: str
    entry_name: str
    created_at: datetime


class GroupExtensionRow(SubjectRow):
    """`org_group_extensions`: an extension they switched on for a group."""

    group_id: str
    extension_id: str
    enabled_at: datetime


class CreatedAppRow(SubjectRow):
    """`org_apps` they registered or own."""

    app_id: str
    owner_user_id: str | None
    created_by_user_id: str | None
    created_at: datetime


class AppExtensionRow(SubjectRow):
    """`org_app_extensions`: an extension they switched on for an app."""

    app_id: str
    extension_id: str
    enabled_at: datetime


class VmProfileRow(SubjectRow):
    """`vm_profiles` they created or last changed."""

    id: str
    name: str
    created_at: datetime
    updated_at: datetime


class VmProfileRequestRow(SubjectRow):
    """`vm_profile_requests` they decided."""

    subject_kind: str
    subject_id: str
    state: str
    decided_at: datetime | None
    reason: str | None


class VmCommandRow(SubjectRow):
    """`vm_commands` they asked for, or that ran in their own VM."""

    id: str
    producer_kind: str
    producer_id: str
    command: str
    requested_by_kind: str
    requested_by_id: str
    requested_as: str
    state: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    exit_code: int | None
    output: str | None
    error: str | None


class VmJobRow(SubjectRow):
    """`vm_jobs` they asked for or cancelled."""

    id: str
    command: str
    requested_as: str
    created_at: datetime
    cancelled_at: datetime | None


class ProducerEventRow(SubjectRow):
    """`producer_events` they produced."""

    extension_id: str
    kind: str
    event_type: str
    #: JSONB, stored as the producer sent it.
    payload: JsonValue
    thread: str | None
    text: str | None
    title: str | None
    start_run: bool
    #: JSONB, stored as the producer sent it.
    action: JsonValue
    #: JSONB list of attachment ids.
    attachment_ids: JsonValue
    state: str
    failed_reason: str | None
    not_handled_reason: str | None
    created_at: datetime
    delivered_at: datetime | None


class EventAttachmentRow(SubjectRow):
    """`service_attachments` on an event they produced."""

    event_id: str | None
    filename: str
    content_type: str
    size: int
    sha256: str
    created_at: datetime
    expires_at: datetime


class VmCredentialRow(SubjectRow):
    """`vm_credentials` for their VM, made by them, or revoked by them."""

    producer_kind: str
    producer_id: str
    created_by_kind: str
    created_by_id: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    revoked_reason: str | None
    revoked_by_id: str | None


class ApiKeyRow(SubjectRow):
    """`api_keys` they hold or revoked."""

    user_id: str
    name: str
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    revoked_reason: str | None
    revoked_by_id: str | None


class ProducerVolumeRow(SubjectRow):
    """`producer_volumes`: their own VM's growth, or growth they caused."""

    producer_kind: str
    producer_id: str
    gib: int
    grown_by_kind: str
    grown_by_id: str
    grown_as: str
    grown_at: datetime


class McpApprovalRow(SubjectRow):
    """`person_mcp_approvals` they gave."""

    server_name: str
    source: str
    approved_at: datetime


class ReceivedNotificationRow(SubjectRow):
    """`notifications` addressed to them."""

    id: str
    source: str
    kind: str
    title: str
    body: str | None
    actor_user_id: str | None
    actor_email: str | None
    #: JSONB: what the notification points at, shaped per kind.
    target: JsonValue
    created_at: datetime
    delivered_at: datetime | None
    read_at: datetime | None


class ProducerExportRow(SubjectRow):
    """`producer_exports` of their data, or ones they asked for."""

    producer_kind: str
    producer_id: str
    requested_by_user_id: str
    requested_at: datetime
    state: str
    finished_at: datetime | None


class RevocationRow(SubjectRow):
    """`revocations` of their tokens, here or everywhere."""

    subject_id: str
    reason: str
    created_at: datetime


class LegalHoldRow(SubjectRow):
    """`legal_holds` on them, or placed by them."""

    user_id: str
    reason: str
    placed_by_user_id: str
    placed_at: datetime


class AppLegalHoldRow(SubjectRow):
    """`app_legal_holds` they placed."""

    app_id: str
    reason: str
    placed_by_user_id: str
    placed_at: datetime


class ProducerFeedRow(SubjectRow):
    """`producer_feed`: what the gateway was last told about their VM."""

    producer_kind: str
    producer_id: str
    active: bool
    removed: bool
    removed_at: datetime | None
    erase_at: datetime | None
    changed_at: datetime
    seen_removed_at: datetime | None
    erased: bool
    legal_hold: bool
    erasure_done_at: datetime | None
    discarded_by_user_id: str | None
    discarded_at: datetime | None


class InferenceNodeRow(SubjectRow):
    """`inference_nodes` they registered."""

    node_id: str
    base_url: str
    created_at: datetime


class ThingsTheyCreated(BaseModel):
    """Everything one person authored, owned, registered or decided in one organisation.

    The field order is the wire order and matches the order
    `SubjectAccessDB.things_they_created` reads them in. Adding a group
    means adding a field here **and** its query there — every field is
    required, so forgetting either side fails loudly.
    """

    model_config = ConfigDict(frozen=True, extra='forbid')

    groups: list[CreatedGroupRow]
    group_entries: list[GroupEntryRow]
    group_extensions: list[GroupExtensionRow]
    apps: list[CreatedAppRow]
    app_extensions: list[AppExtensionRow]
    vm_profiles: list[VmProfileRow]
    vm_profile_requests: list[VmProfileRequestRow]
    vm_commands: list[VmCommandRow]
    vm_jobs: list[VmJobRow]
    events: list[ProducerEventRow]
    event_attachments: list[EventAttachmentRow]
    vm_credentials: list[VmCredentialRow]
    api_keys: list[ApiKeyRow]
    producer_volumes: list[ProducerVolumeRow]
    mcp_approvals: list[McpApprovalRow]
    notifications: list[ReceivedNotificationRow]
    producer_exports: list[ProducerExportRow]
    revocations: list[RevocationRow]
    legal_holds: list[LegalHoldRow]
    app_legal_holds: list[AppLegalHoldRow]
    producer_feed: list[ProducerFeedRow]
    inference_nodes: list[InferenceNodeRow]
