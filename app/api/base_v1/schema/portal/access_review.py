"""The access review report.

AC-8 in `COMPLIANCE_TRACKER.md` — SOC 2 CC6.3, ISO 27001 A 5.18. What an
auditor samples is not "is there access control" (that is CC6.1) but "is
access still appropriate", so the report is built around the signals that
answer it: role, status, how long ago somebody was added, and when they
last signed in.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.db.sql.schemas.team import MembershipReviewRow

# `never_signed_in` and `dormant` are mutually exclusive: somebody who has
# never signed in is not dormant, they never arrived. Keeping them
# separate matters because they have different remedies — chase the
# invitation, or revoke the access.
AccessFlag = Literal['never_signed_in', 'dormant', 'invitation_pending', 'suspended']


class AccessReviewRow(BaseModel):
    """One person's access to this organisation."""

    user_id: str | None = Field(
        default=None,
        description='Null for an invitation that has not been claimed — there is no user yet.',
    )
    email: str | None = Field(default=None, description="The account's address, or the invited address if unclaimed.")
    full_name: str | None = None
    auth_provider: str | None = Field(
        default=None, description='How they authenticate. Null for an unclaimed invitation.'
    )

    role: str
    status: str

    granted_at: datetime | None = Field(default=None, description='When the membership row was created.')
    granted_by: str | None = Field(default=None, description='Who invited them, where recorded.')

    last_sign_in_at: datetime | None = Field(
        default=None, description='Null means not since this was first recorded — see the flags.'
    )
    days_since_sign_in: int | None = None

    flags: list[AccessFlag] = Field(
        default_factory=list,
        description='Why this row is worth a second look. Empty is the ordinary case.',
    )


class AccessReview(BaseModel):
    """The report, plus the counts an auditor writes down."""

    org_id: str
    generated_at: datetime
    dormant_after_days: int = Field(description='The dormancy threshold this report was generated with.')

    total: int
    active: int
    needs_review: int = Field(description='Rows carrying at least one flag. The number to sample from.')

    rows: list[AccessReviewRow] = Field(default_factory=list)

    @staticmethod
    def flags_for(
        *,
        status: str,
        last_sign_in_at: datetime | None,
        days_since: int | None,
        dormant_after_days: int,
    ) -> list[AccessFlag]:
        """Which signals apply to one row.

        A pure function so the policy is testable without a database, and
        so "what counts as dormant" has one definition rather than being
        re-decided in a template.
        """
        flags: list[AccessFlag] = []
        if status == 'invited':
            flags.append('invitation_pending')
        elif status not in ('active',):
            # `suspended`, or any state a later migration adds: not active
            # and not a pending invitation is worth a look either way.
            flags.append('suspended')

        if status == 'active':
            if last_sign_in_at is None:
                flags.append('never_signed_in')
            elif days_since is not None and days_since >= dormant_after_days:
                flags.append('dormant')

        return flags

    @classmethod
    def of(
        cls,
        *,
        org_id: str,
        rows: list[MembershipReviewRow],
        generated_at: datetime,
        dormant_after_days: int,
    ) -> AccessReview:
        built: list[AccessReviewRow] = []
        for row in rows:
            last = row.last_sign_in_at
            days = (generated_at - last).days if last is not None else None
            built.append(
                AccessReviewRow(
                    user_id=row.user_id,
                    # An unclaimed invitation has no user row, so its
                    # address is the one it was sent to.
                    email=row.email or row.invited_email,
                    full_name=row.full_name,
                    auth_provider=row.auth_provider,
                    role=row.role,
                    status=row.status,
                    granted_at=row.created_at,
                    granted_by=row.invited_by,
                    last_sign_in_at=last,
                    days_since_sign_in=days,
                    flags=cls.flags_for(
                        status=row.status,
                        last_sign_in_at=last,
                        days_since=days,
                        dormant_after_days=dormant_after_days,
                    ),
                )
            )

        return cls(
            org_id=org_id,
            generated_at=generated_at,
            dormant_after_days=dormant_after_days,
            total=len(built),
            active=sum(1 for r in built if r.status == 'active'),
            needs_review=sum(1 for r in built if r.flags),
            rows=built,
        )
