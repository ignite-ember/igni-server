"""What the subject access reads about a person's account, standing and activity return.

One row model per statement in `SubjectAccessDB`. The rows for what a
person made, and the `ThingsTheyCreated` envelope that groups them, are
`subject_access_created.py`.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SubjectRow(BaseModel):
    """One row of a subject access read: every column its statement selects, and nothing else.

    A subclass's fields are exactly its statement's columns, in the same
    order — the order is the export's wire order. Every field is required
    and extras are refused, so a column added to a statement without a
    field, or a field with no column, fails on the first export instead
    of shipping a report that quietly lacks or invents a value.
    """

    model_config = ConfigDict(frozen=True, extra='forbid')


class AccountRow(SubjectRow):
    """`users`: the account matched on the address."""

    user_id: str
    email: str
    full_name: str | None
    auth_provider: str
    created_at: datetime
    updated_at: datetime
    last_sign_in_at: datetime | None
    tokens_valid_after: datetime | None
    deactivated_at: datetime | None
    is_super_admin: bool


class DeploymentRoleRow(SubjectRow):
    """`user_roles`: where they stand in the deployment."""

    role: str
    status: str
    email: str | None
    full_name: str | None
    pending_email: str | None
    assigned_by: str | None
    created_at: datetime
    updated_at: datetime


class OrganisationMembershipRow(SubjectRow):
    """`org_memberships`: one organisation they belong to."""

    org_id: str
    role: str
    status: str
    invited_email: str | None
    invited_by: str | None
    created_at: datetime
    updated_at: datetime


class OrganisationOwnedRow(SubjectRow):
    """`organizations`: one organisation they own."""

    id: str
    slug: str
    display_name: str
    created_at: datetime


class GroupMembershipRow(SubjectRow):
    """`org_group_members`: one group they are in."""

    group_id: str
    added_by_user_id: str | None
    added_at: datetime


class ModelUsageRow(SubjectRow):
    """`chat_usage`: one call they made to a model."""

    org_id: str | None
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    duration: float | None
    stream: int
    created_at: datetime


class AuditEventRow(SubjectRow):
    """`audit_events`: one request, from either trail.

    The same columns whether the row was found by the actor's id or by
    their address, so both activity reads return this.
    """

    occurred_at: datetime
    method: str
    route: str
    target_type: str | None
    target_id: str | None
    status_code: int
    outcome: str
    client_host: str | None
    forwarded_for: str | None
    user_agent: str | None
