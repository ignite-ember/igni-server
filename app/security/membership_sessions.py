"""A session for an organisation ends with the membership it was signed in under.

Removing a person from an organisation deletes their membership; adding
them again makes a new one (a new incarnation, `docs/service-identity.md`
§11.3). A session pinned to that organisation and issued before the
membership it would now act under began is refused: removal ends the
person's sessions for that organisation — and only that one — and they
never come back with a re-add. Their account's other sessions are
untouched.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException, status

from app.db.sql.models.org_membership import OrgMembership


class MembershipSessions:
    @staticmethod
    def predates(membership: OrgMembership, issued_at: int | None) -> bool:
        """Whether a session issued at *issued_at* (Unix seconds) is older than the membership."""
        began: datetime = membership.created_at
        return issued_at is not None and issued_at < int(began.timestamp())

    @classmethod
    def require_current(cls, membership: OrgMembership, issued_at: int | None) -> None:
        if cls.predates(membership, issued_at):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='This session was for an earlier membership of this organization; sign in again',
                headers={'WWW-Authenticate': 'Bearer'},
            )
