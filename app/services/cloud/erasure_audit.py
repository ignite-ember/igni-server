"""Every erasure the feed schedules, moves or stops is in the organisation's audit trail."""

from __future__ import annotations

from app.db.sql.audit_events import AuditEventDB, AuditRecord
from app.db.sql.schemas.producer_feed import Scheduled

#: How these rows read in the trail: not a request, the feed's own act.
ROUTE = 'producer-feed/erasure'


class ErasureAudit:
    async def record(self, scheduled: list[Scheduled]) -> None:
        trail = AuditEventDB()
        for event in scheduled:
            await trail.record(
                AuditRecord(
                    method='SYSTEM',
                    route=ROUTE,
                    status_code=200,
                    org_id=event.org_id if event.org_id != '*' else None,
                    target_type=f'{event.producer_kind}_vm',
                    target_id=event.producer_id,
                    detail={
                        'incarnation': event.incarnation,
                        'erase_at': event.erase_at.isoformat() if event.erase_at else None,
                        'why': event.why,
                    },
                )
            )
