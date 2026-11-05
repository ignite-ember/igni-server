"""Pydantic schemas for portal team management."""

from datetime import datetime

from pydantic import BaseModel, computed_field, ConfigDict, Field


class TeamMemberResponse(BaseModel):
    """Team member with portal role and usage stats"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    email: str | None = None
    full_name: str | None = None
    role: str
    status: str = 'active'
    #: Chat requests over the last 30 days. Each one is a row in chat
    #: usage; nothing records a session.
    total_requests: int = 0
    total_tokens: int = 0
    last_active: datetime | None = None
    joined_at: datetime
    assigned_by: str | None = None

    # Marked deprecated in the schema only: pydantic's `deprecated=` warns
    # on every serialisation, once per member per Team page load.
    # mypy cannot type a decorator over a property; the ignore is the one pydantic documents.
    @computed_field(json_schema_extra={'deprecated': True})  # type: ignore[prop-decorator]
    @property
    def total_sessions(self) -> int:
        """The old name of `total_requests`, still sent for clients that read it."""
        return self.total_requests


class TeamMemberList(BaseModel):
    """List of team members"""

    members: list[TeamMemberResponse]
    total: int


class RoleChangeRequest(BaseModel):
    """Request to change a user's role"""

    role: str = Field(..., description='New role: admin or member')


class RoleChangeResponse(BaseModel):
    """Response after role change"""

    user_id: str
    old_role: str
    new_role: str


class StatusChangeRequest(BaseModel):
    """Request to activate or deactivate a user"""

    status: str = Field(..., description='New status: active or deactivated')


class StatusChangeResponse(BaseModel):
    """Response after status change"""

    user_id: str
    old_status: str
    new_status: str


class InvitedMemberResponse(TeamMemberResponse):
    """An invited member, plus the link they use to claim the invite.

    ``invite_url`` is always populated, not only when email is off. It
    is not a secret — the invite is claimed by signing up with the
    invited address, so the URL is derivable from the address — and an
    admin who wants to send it through Slack rather than wait for mail
    should not have to reconstruct it.

    ``emailed`` says whether we also sent it. False means this
    deployment has no relay and passing the link on is the admin's job.
    """

    invite_url: str
    emailed: bool = True


class InviteMemberRequest(BaseModel):
    """Request to invite a new team member"""

    email: str = Field(..., description='Email address of the user to invite')
    role: str = Field('member', description='Role: admin or member')


class SessionsWithdrawnResponse(BaseModel):
    """DEC-10/B5. What the revocation actually did.

    ``tokens_valid_after`` is echoed because a person who has just cut
    off every device they own deserves to be told from when, and because
    it is the value the database holds rather than a second reading of
    the clock. Support answering "why am I signed out on my laptop" reads
    this field.

    There is no count of sessions ended. There could not be one: the
    design is a watermark, not a session table, which is the whole reason
    revocation costs one column and no bookkeeping — so a number here
    would have to be invented, and an invented number in a security
    response is worse than its absence.
    """

    user_id: str
    tokens_valid_after: datetime
