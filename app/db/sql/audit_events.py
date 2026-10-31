"""Writing and reading the audit trail.

There is deliberately no `update` here, and there never should be — but
the guarantee lives in the database (`audit_events_no_update`), not in
this file's omissions. See the migration for why that distinction matters.

Two trails. An action inside an organisation goes to that organisation's
own database, with the rest of its data; an action with no organisation —
a sign-in, a super admin's change to the deployment — goes to the shared
plane. `org_id` on the record decides which, and reading takes the same
argument: an organisation, or `None` for the deployment's own trail.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.audit_event import AuditColumns, AuditEvent, DeploymentAuditEvent
from app.db.sql.tenancy import TenantData
from app.monitoring import AUDIT_EVENTS_WRITTEN, AUDIT_WRITE_FAILURES

logger = logging.getLogger(__name__)

# Anything at or above this is a refusal rather than a failure. 4xx that
# is not 401/403 is a bad request, which is noise; the two that matter are
# the ones that say "you were not allowed".
_DENIED_STATUSES = frozenset({401, 403})


def classify(status_code: int) -> str:
    """Coarse outcome, so a reviewer can filter without knowing HTTP.

    `denied` is separated from `error` because a run of refusals against
    one actor is the pattern worth alerting on, and it is invisible if
    everything non-2xx is lumped together.
    """
    if status_code in _DENIED_STATUSES:
        return 'denied'
    if 200 <= status_code < 400:
        return 'success'
    return 'error'


@dataclass(frozen=True)
class AuditRecord:
    """What happened, as the middleware or a handler saw it."""

    method: str
    route: str
    status_code: int
    actor_user_id: str | None = None
    actor_email: str | None = None
    org_id: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    client_host: str | None = None
    forwarded_for: str | None = None
    user_agent: str | None = None
    detail: dict[str, Any] | None = None


class AuditEventDB(TenantData):
    """The audit trail: each organisation's, and the deployment's."""

    def _trail(self, org_id: str | None) -> AbstractAsyncContextManager[AsyncSession]:
        """The session for one trail: an organisation's database, or the shared plane."""
        return self.session_scope(org_id) if org_id is not None else SQLAlchemySessionManager().session_scope()

    @staticmethod
    def _table(org_id: str | None) -> type[AuditEvent] | type[DeploymentAuditEvent]:
        return AuditEvent if org_id is not None else DeploymentAuditEvent

    async def record(self, event: AuditRecord) -> None:
        """Append one event.

        **Never raises.** A failure to write the audit row is logged at
        ERROR and swallowed, which is a real trade-off and worth stating:
        the alternative is refusing the request, which turns a database
        hiccup into an outage on every privileged endpoint at once.

        The argument for failing open is that this table lives in the same
        database the request itself just used — so a write failure here
        almost always means the request failed too, and the interesting
        case (a successful mutation with no record of it) is rare. The
        argument against is that it is exactly the case an attacker would
        want. The compromise is that it is loud: `AUDIT WRITE FAILED`
        appears in the log with the event that was lost, so a deployment
        shipping logs to an aggregator can alert on it.
        """
        try:
            async with self._trail(event.org_id) as session:
                session.add(
                    self._table(event.org_id)(
                        id=str(uuid.uuid4()),
                        occurred_at=datetime.now(timezone.utc),
                        actor_user_id=event.actor_user_id,
                        actor_email=event.actor_email,
                        org_id=event.org_id,
                        method=event.method,
                        route=event.route,
                        target_type=event.target_type,
                        target_id=event.target_id,
                        status_code=event.status_code,
                        outcome=classify(event.status_code),
                        client_host=event.client_host,
                        forwarded_for=event.forwarded_for,
                        user_agent=event.user_agent,
                        detail=event.detail,
                    )
                )
            AUDIT_EVENTS_WRITTEN.inc()
        except Exception:
            # Counted as well as logged. "Alertable in principle" was the
            # whole of LG-6's evidence for a year — a log line nobody had
            # written a rule for. `igni_audit_write_failures_total` is
            # that rule's left-hand side.
            AUDIT_WRITE_FAILURES.inc()
            # The event itself goes in the message: a lost audit row that
            # leaves no trace at all is the worst of both worlds.
            logger.exception(
                'AUDIT WRITE FAILED — %s %s by %s (%s) → %s',
                event.method,
                event.route,
                event.actor_user_id or 'anonymous',
                event.actor_email or '-',
                event.status_code,
            )

    async def list_events(
        self,
        *,
        org_id: str | None,
        actor_user_id: str | None = None,
        target_id: str | None = None,
        outcome: str | None = None,
        limit: int = 100,
        before: datetime | None = None,
        since: datetime | None = None,
        oldest_first: bool = False,
    ) -> list[AuditColumns]:
        """Keyset-paginated on `occurred_at`. Newest first by default.

        One trail: the organisation's, or with `None` the deployment's.
        Required rather than defaulted, so no caller reads the
        deployment's trail by forgetting to name an organisation.

        Keyset rather than `OFFSET`: an audit table is append-heavy and
        read rarely, so an offset scan gets slower exactly as the table
        becomes worth reading. `before` is the `occurred_at` of the last
        row a caller saw.

        `oldest_first` with `since` is the other direction, for a
        collector resuming an export rather than a person reviewing. The
        order has to match the cursor: ascending rows with a descending
        cursor is an importer that re-reads the newest page forever.
        """
        table = self._table(org_id)
        if oldest_first:
            query = select(table).order_by(table.occurred_at.asc(), table.id.asc())
        else:
            query = select(table).order_by(table.occurred_at.desc(), table.id.desc())

        if actor_user_id is not None:
            query = query.where(table.actor_user_id == actor_user_id)
        if target_id is not None:
            query = query.where(table.target_id == target_id)
        if outcome is not None:
            query = query.where(table.outcome == outcome)
        if before is not None:
            query = query.where(table.occurred_at < before)
        if since is not None:
            # Inclusive, so a collector that stores the last timestamp it
            # ingested re-reads one row rather than skipping one. A
            # duplicate is harmless — the id is stable and every sane
            # importer deduplicates — while a gap in an audit trail is
            # the thing this table exists to not have.
            query = query.where(table.occurred_at >= since)

        # 1000 for a page, more for an export: a SIEM backfill of a year
        # in 1000-row batches is 300 round trips nobody will script
        # correctly.
        query = query.limit(max(1, min(limit, 50_000 if oldest_first else 1000)))

        async with self._trail(org_id) as session:
            return list((await session.execute(query)).scalars().all())

    async def prune(self, *, older_than_days: int) -> int:
        """Delete events older than the retention window; returns the count.

        `older_than_days <= 0` is a no-op meaning "keep everything", which
        is the default. Retention is a policy decision an operator makes,
        and a default that silently discarded audit history would be the
        wrong way round — GDPR Article 5(1)(e) asks for a limit, not for
        deletion by surprise.
        """
        if older_than_days <= 0:
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        removed = 0
        trails: list[str | None] = [None, *await self.each_tenant()]
        for org_id in trails:
            table = self._table(org_id)
            async with self._trail(org_id) as session:
                result = await session.execute(delete(table).where(table.occurred_at < cutoff))
                # ``rowcount`` is on the cursor result rather than the base
                # ``Result`` mypy infers for ``execute``; a DELETE always
                # produces the former.
                removed += int(getattr(result, 'rowcount', 0) or 0)

        if removed:
            logger.info(
                'Audit retention: removed %d event(s) older than %d day(s)',
                removed,
                older_than_days,
            )
        return removed
