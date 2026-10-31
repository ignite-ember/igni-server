"""Rows the team store returns for an access review."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MembershipReviewRow(BaseModel):
    """One membership, joined to whatever is known about the person.

    Named for the membership rather than the review: `AccessReviewRow`
    in app/api/base_v1/schema/portal/access_review.py is the shape the
    *report* carries, with flags and a dormancy calculation on it. Two
    different contracts sharing a name one import apart is how the next
    person picks the wrong one.

    **Almost everything is optional, and that is the point.** The query
    behind this is a LEFT OUTER JOIN: a membership with
    `status='invited'` has an `invited_email` and no user row at all
    until the invitation is claimed. Those are the most interesting
    rows in a review — access granted to somebody who never arrived —
    so the model has to admit them rather than reject them.

    `role`, `status` and `created_at` come from the membership itself
    and are always there.
    """

    user_id: str | None = None
    role: str
    status: str
    invited_email: str | None = None
    invited_by: str | None = None
    created_at: datetime
    email: str | None = None
    full_name: str | None = None
    auth_provider: str | None = None
    last_sign_in_at: datetime | None = None
