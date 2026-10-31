"""Pydantic schemas for portal user."""

from datetime import datetime

from pydantic import BaseModel, Field


class PortalAccount(BaseModel):
    """Somebody signed in, whether or not they are in an organisation yet.

    What the routes that make sense without one depend on: the account's
    own profile, the organisations it can see, and — for a super admin —
    creating organisations. Accounts start with no organisation; a
    person joins one by invitation.
    """

    user_id: str
    email: str | None = None
    full_name: str | None = None
    is_super_admin: bool = False
    #: The session token's `iat`. What a sign-in to a registered client
    #: carries forward, so withdrawing this session reaches it too.
    session_issued_at: int | None = Field(default=None, exclude=True)
    #: The active organisation, when there is one.
    org_id: str | None = None
    org_role: str | None = None


class PortalUser(PortalAccount):
    """Authenticated portal user in an active organisation — what org-scoped routes depend on."""

    org_id: str
    org_role: str  # role within the active org

    @property
    def role(self) -> str:
        """Backward-compatible alias for org_role."""
        return self.org_role


class OrgSummary(BaseModel):
    """Lightweight org info for the org switcher."""

    id: str
    slug: str
    display_name: str
    role: str


class UserMeResponse(BaseModel):
    """Current user profile"""

    user_id: str
    email: str | None = None
    full_name: str | None = None
    role: str | None = None
    created_at: datetime | None = None
    pending_email: str | None = None
    auth_provider: str = 'email'
    org_id: str | None = None
    org_slug: str | None = None
    org_display_name: str | None = None
    organizations: list[OrgSummary] = []
    #: May create organisations and make other super admins.
    is_super_admin: bool = False


class UpdateProfileRequest(BaseModel):
    """Fields the user can update on their own profile.

    Email changes are intentionally not exposed — users must go through
    support to update their address.
    """

    full_name: str | None = None


# ``SetPasswordRequest`` was here. Its endpoint was an account-takeover
# primitive and is gone — see the note in
# ``app/api/base_v1/endpoints/portal/user.py`` and F110. Worth one line
# here as well: this schema never applied
# ``_validate_password_strength``, which every other password field in
# the application does, so it was also the one way to set a password the
# policy would reject.
