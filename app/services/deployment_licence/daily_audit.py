"""Once a day, while a licence needs attention, the deployment's audit trail says so.

An expiring, lapsed or read-only module is recorded in the deployment's
own trail (no organisation) once per UTC day, with every module's state,
so an auditor reading the trail sees when the customer was told and for
how long. It runs in every replica; a Postgres advisory lock and a look
at today's events keep it to one record a day.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from datetime import datetime, timezone
import logging

from app.db.sql.advisory_locks import AdvisoryLockDB
from app.db.sql.audit_events import AuditEventDB, AuditRecord
from app.services.deployment_licence.service import DeploymentLicence

logger = logging.getLogger(__name__)

#: The route an audit record of this job carries; how today's record is found again.
DAILY_ROUTE = 'licence:daily'
#: Arbitrary, stable, and distinct from every other advisory lock igni server takes.
_LOCK_KEY = 0x1911_3_11CE
#: How often the job looks. Hourly, so a replica that starts at 23:59 still records the day.
_EVERY_SECONDS = 3600


class LicenceDailyAudit:
    """The job, and the loop that runs it."""

    def __init__(
        self,
        *,
        licence: Callable[[], DeploymentLicence] = DeploymentLicence.current,
        audit: AuditEventDB | None = None,
        locks: AdvisoryLockDB | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._licence = licence
        self._audit = audit or AuditEventDB()
        self._locks = locks or AdvisoryLockDB()
        self._now = now
        self._task: asyncio.Task[None] | None = None

    async def run_once(self) -> bool:
        """Record today's event if one is due and none is there yet. True when this call wrote it."""
        notice = self._licence().notice()
        if not notice.warnings:
            return False
        if not await self._locks.try_acquire(_LOCK_KEY):
            return False
        try:
            overview = self._licence().overview()
            target = overview.licence_id or 'none'
            midnight = self._now().replace(hour=0, minute=0, second=0, microsecond=0)
            today = await self._audit.list_events(org_id=None, target_id=target, since=midnight, limit=1000)
            if any(event.route == DAILY_ROUTE for event in today):
                return False
            for warning in notice.warnings:
                logger.warning('licence: %s is %s: %s', warning.module.value, warning.state.value, warning.message)
            await self._audit.record(
                AuditRecord(
                    method='SYSTEM',
                    route=DAILY_ROUTE,
                    status_code=200,
                    target_type='licence',
                    target_id=target,
                    detail={
                        'customer': overview.customer,
                        'modules': [warning.model_dump(mode='json') for warning in notice.warnings],
                    },
                )
            )
            return True
        finally:
            await self._locks.release(_LOCK_KEY)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name='licence-daily-audit')

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001 — a top-level loop: logged, and the next hour tries again
                logger.exception('licence: the daily audit record could not be written')
            await asyncio.sleep(_EVERY_SECONDS)
