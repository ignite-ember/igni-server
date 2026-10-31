"""Reading the audit trail.

An audit trail nobody can read is not a control. SOC 2 CC7.2 and ISO 27001
Annex A 8.15 both ask for records that are *reviewed*, and the practical
test is whether an administrator can answer "what did this person do" and
"what happened to this thing" without database access.

## Scoped to the caller's organisation

`org_id` is forced from the token rather than accepted as a parameter, so
an admin of one organisation cannot read another's activity — §38's sweep
found 27 of 30 endpoints already doing this and the three exceptions were
deliberate. This is not one of them.

It is also a different database. Each organisation's trail lives in its
own; events with **no** organisation — sign-ins, failed ones included, and
super admins' changes to the deployment — are in the deployment's trail in
the shared plane, which no organisation's admin reads from here.

## Admin-only

Reading the trail shows who did what and from where, which is a map of
staff activity. `require_admin` gates it for the same reason the
inference page is gated.
"""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.api.base_v1.schema.portal.audit import AuditEventPage, AuditEventView
from app.api.base_v1.schema.portal.user import PortalUser
from app.db.sql.audit_events import AuditEventDB
from app.middlewares.audit import set_audit_detail
from app.security.portal_auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter()

_audit = AuditEventDB()


@router.get(
    '/audit-events',
    response_model=AuditEventPage,
    description=(
        'Privileged activity in this organisation, newest first. '
        'Paginate with `before`, using the `occurred_at` of the last row seen.'
    ),
)
async def list_audit_events(
    actor_user_id: Annotated[str | None, Query(description='Only this actor')] = None,
    target_id: Annotated[str | None, Query(description='Only actions on this entity')] = None,
    outcome: Annotated[
        Literal['success', 'denied', 'error'] | None,
        Query(description='Only this outcome. `denied` is the one worth watching.'),
    ] = None,
    before: Annotated[datetime | None, Query(description='Keyset cursor: the last `occurred_at` you saw')] = None,
    limit: Annotated[int, Query(ge=1, le=500, description='Page size')] = 100,
    current_user: PortalUser = Depends(require_admin),
) -> AuditEventPage:
    rows = await _audit.list_events(
        org_id=current_user.org_id,
        actor_user_id=actor_user_id,
        target_id=target_id,
        outcome=outcome,
        before=before,
        limit=limit,
    )

    events = [AuditEventView.of(row) for row in rows]
    return AuditEventPage(
        events=events,
        # The cursor for the next page, or None when this was the last one.
        # Returned rather than left to the caller to derive, because
        # deriving it means knowing the ordering is by `occurred_at`.
        next_before=events[-1].occurred_at if len(events) == limit else None,
    )


@router.get(
    '/audit-events/export',
    description=(
        'The same events as JSON Lines, for a SIEM or an archive. One object per line, '
        'oldest first, streamed. Paginate with `since` using the last `occurred_at` you ingested.'
    ),
    responses={200: {'content': {'application/x-ndjson': {}}}},
)
async def export_audit_events(
    request: Request,
    since: Annotated[
        datetime | None,
        Query(description='Only events at or after this time. Use the last one you ingested.'),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=50_000, description='Maximum rows in this batch')] = 10_000,
    current_user: PortalUser = Depends(require_admin),
) -> StreamingResponse:
    """Bulk read, for whatever the customer ingests logs into.

    LG-7's gap named "no SIEM integration", and the reviewable page above
    does not close it: paging 100 rows at a time through a UI-shaped
    envelope is not how anybody loads a year of audit records into
    Splunk.

    **JSON Lines, not JSON.** A single array means the consumer buffers
    the whole response before it can parse anything, and every log
    shipper in existence already reads one object per line.

    **Oldest first, unlike the page above.** A person reviewing wants the
    newest event; a collector wants to resume from where it stopped, and
    that only works if the order matches the cursor. Getting this
    backwards produces an importer that silently re-ingests the same
    newest page forever.

    **Streamed, so the batch size is a memory bound and not a promise.**
    The response ends when the rows run out; if you get `limit` lines,
    there are probably more, and the last line's `occurred_at` is the
    next `since`. That is a real cap and it is documented rather than
    silent — the F96 lesson.

    Audited by F90's rule like everything else under `/v1/portal`: an
    export of the audit trail is itself a privileged read, and the record
    of it is the answer to "who took a copy".
    """
    rows = await _audit.list_events(
        org_id=current_user.org_id,
        since=since,
        limit=limit,
        oldest_first=True,
    )

    # Counts, never the rows. The audit trail must not become the place
    # a copy of the audit trail lives.
    set_audit_detail(request, {'exported': len(rows), 'since': since.isoformat() if since else None})

    def lines():
        for row in rows:
            yield AuditEventView.of(row).model_dump_json() + '\n'

    return StreamingResponse(lines(), media_type='application/x-ndjson')
