"""What the portal shows for one audit event."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AuditEventView(BaseModel):
    """One recorded action.

    `forwarded_for` is returned alongside `client_host` rather than
    instead of it, matching how the row is stored: one is the peer that
    connected, the other is a header the client chose. Presenting only the
    header would show a spoofable value as fact.
    """

    id: str
    occurred_at: datetime

    actor_user_id: str | None = None
    actor_email: str | None = None
    # Returned even though the endpoint scopes to the caller's own
    # organisation: an exported page of events should say which
    # organisation it describes rather than leaving that implicit, and
    # events with no organisation (a failed sign-in) are distinguishable
    # from scoped ones only if this is present.
    org_id: str | None = None

    method: str
    route: str
    target_type: str | None = None
    target_id: str | None = None

    status_code: int
    outcome: str

    client_host: str | None = None
    forwarded_for: str | None = None
    user_agent: str | None = None

    detail: dict[str, Any] | None = None

    @classmethod
    def of(cls, row: Any) -> AuditEventView:
        return cls(
            id=str(row.id),
            occurred_at=row.occurred_at,
            actor_user_id=str(row.actor_user_id) if row.actor_user_id else None,
            actor_email=str(row.actor_email) if row.actor_email else None,
            org_id=str(row.org_id) if row.org_id else None,
            method=str(row.method),
            route=str(row.route),
            target_type=str(row.target_type) if row.target_type else None,
            target_id=str(row.target_id) if row.target_id else None,
            status_code=int(row.status_code),
            outcome=str(row.outcome),
            client_host=str(row.client_host) if row.client_host else None,
            forwarded_for=str(row.forwarded_for) if row.forwarded_for else None,
            user_agent=str(row.user_agent) if row.user_agent else None,
            detail=row.detail,
        )


class AuditEventPage(BaseModel):
    """A page of events, newest first."""

    events: list[AuditEventView] = Field(default_factory=list)
    next_before: datetime | None = Field(
        default=None,
        description='Pass as `before` for the next page. Null when this was the last page.',
    )
