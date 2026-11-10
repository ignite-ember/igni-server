"""The facts each route adds to its audit row's `detail`.

`audit_events.detail` is retained for the whole window (F90), so every
key that lands in it is written down here rather than agreed between a
dict literal and whoever reads the row. Never a request body (F83): only
small, deliberate facts.
"""

from typing import Any, Literal

from pydantic import BaseModel

from app.api.base_v1.schema.producer_events import NotHandledEvent


class AppRegisteredAudit(BaseModel):
    app: str
    thumbprint: str


class KeysReplacedAudit(BaseModel):
    thumbprint: str


class AppDiscardedAudit(BaseModel):
    why: Literal['discarded'] = 'discarded'
    #: Where the VM's erasure stands; null when it never existed.
    erasure: str | None
    fresh_app: str | None


class GroupExtensionSetAudit(BaseModel):
    extension: str
    enabled: bool


class ClientDecisionAudit(BaseModel):
    client: str
    decision: str
    thumbprint: str


class HeldUpdateDecisionAudit(BaseModel):
    client: str
    decision: str
    extension: str
    version: str
    sha256: str
    approved_version: str | None
    #: The grants the held version adds, as the review shows them.
    added: list[str]


class AuditExportAudit(BaseModel):
    """Counts, never the rows: the trail must not hold a copy of itself."""

    exported: int
    since: str | None


class VolumeGrownAudit(BaseModel):
    gib: int


class ExportRequestedAudit(BaseModel):
    person: str
    incarnation: str


class LegalHoldAudit(BaseModel):
    reason: str


class ErasureDoneAudit(BaseModel):
    """The gateway erased a removed incarnation's VM: the feed's own row, not a request's."""

    incarnation: str
    #: When the erasure was due, ISO 8601; null when a legal hold had stopped it.
    erase_at: str | None
    why: Literal['erased'] = 'erased'
    by_client: str
    #: What the node answered, part by part (bounded by `ErasureDoneBody`).
    summary: dict[str, Any]


class MemberVmDiscardedAudit(BaseModel):
    why: Literal['discarded'] = 'discarded'
    incarnation: str | None


class VmProfileSavedAudit(BaseModel):
    name: str
    version: str
    internet: bool


class VmProfileAssignedAudit(BaseModel):
    vm_profile_id: str | None


class VmProfileDecisionAudit(BaseModel):
    subject: str
    state: str
    version: str
    packages_version: str
    #: Whether the decision grants (or refuses) the internet: a security grant (§12.4).
    internet: bool
    profile_id: str | None


class VmProfileAskedAudit(BaseModel):
    app_id: str
    version: str
    packages_version: str
    internet: bool
    changed: bool
    state: str


class TokenGrantAudit(BaseModel):
    grant: Literal['api_key'] = 'api_key'


class ItemDeliveredAudit(BaseModel):
    producer: str
    kind: str
    type: str
    thread: str | None
    attachments: list[str]


class AttachmentUploadedAudit(BaseModel):
    filename: str
    size: int


class EventsNotHandledAudit(BaseModel):
    not_handled: list[NotHandledEvent]


class EventsFailedAudit(BaseModel):
    event_ids: list[str]
    reason: str


class VmCommandAudit(BaseModel):
    producer: str
    org: str


class VmJobAudit(BaseModel):
    scope: str
    scope_id: str | None
    organisations: int


class VmCredentialPickedUpAudit(BaseModel):
    producer: str
    org: str


class NodeRegisteredAudit(BaseModel):
    """The address prompts will now go to; never the node's api_key.

    The principal is named because these routes authenticate with a
    shared node token, so `actor_user_id` is null: "the node token did
    this" is the whole of what can be said.
    """

    principal: Literal['node-token'] = 'node-token'
    node_id: str
    base_url: str
    models: list[str]
    replaced: bool


class NodeRemovedAudit(BaseModel):
    principal: Literal['node-token'] = 'node-token'
    node_id: str
