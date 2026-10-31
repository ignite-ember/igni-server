"""The access review an auditor samples.

AC-8 in `COMPLIANCE_TRACKER.md` — SOC 2 CC6.3, ISO 27001 A 5.18: access is
removed when it is no longer needed. The control is not "does access
control exist", which is CC6.1 and was verified by §38's sweep; it is
whether somebody looks at who still has access and acts on it.

Nothing supported that. There was no way to answer "who holds which role
in this organisation, and are they still using it" short of SQL, so the
review could not be performed, let alone evidenced.

## Admin-only, and scoped to the caller's own organisation

`org_id` comes from the token rather than a parameter, the property §38
established across 30 endpoints. Everything here — addresses, roles, when
somebody last signed in — is a map of who works there.

## The interesting rows are the ones with flags

`needs_review` is the number to sample from, and the flags are
deliberately separate rather than one "stale" bucket, because they have
different remedies: an unclaimed invitation is chased, a dormant account
is revoked, and somebody who never signed in at all never arrived.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.base_v1.schema.portal.access_review import AccessReview
from app.api.base_v1.schema.portal.user import PortalUser
from app.db.sql.team import TeamDB
from app.security.portal_auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter()

_team_db = TeamDB()

# 90 days is the common default in an access-review policy, and it is a
# *query parameter* rather than a setting so a reviewer can move it
# without a deployment — an auditor asking "show me 30 days" should not
# need a config change.
_DEFAULT_DORMANT_AFTER_DAYS = 90


@router.get(
    '/access-review',
    response_model=AccessReview,
    description=(
        'Who holds access to this organisation, with the signals an access review '
        'acts on: pending invitations, accounts that never signed in, and dormant ones.'
    ),
)
async def access_review(
    dormant_after_days: Annotated[
        int,
        Query(
            ge=1,
            le=3650,
            description='Treat an account with no sign-in in this many days as dormant.',
        ),
    ] = _DEFAULT_DORMANT_AFTER_DAYS,
    current_user: PortalUser = Depends(require_admin),
) -> AccessReview:
    org_id = current_user.org_id or ''
    rows = await _team_db.access_review_rows(org_id)

    return AccessReview.of(
        org_id=org_id,
        rows=rows,
        # Generated once and passed down, so every `days_since_sign_in` in
        # one report is measured from the same instant. Computing it per
        # row would make a large report internally inconsistent.
        generated_at=datetime.now(timezone.utc),
        dormant_after_days=dormant_after_days,
    )
