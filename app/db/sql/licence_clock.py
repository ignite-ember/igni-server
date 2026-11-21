"""The licence's high-water mark on the shared plane (`LicenceClock`), as queries.

Implements `app.services.licence.ClockStore`. Whether a time counts as
seen is the licence core's business (`LicenceClock`); this only reads and
raises what is written down.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.models.audit_event import DeploymentAuditEvent
from app.db.sql.models.licence_clock import LicenceClock

#: The route of the daily licence record (`app.services.deployment_licence.daily_audit.DAILY_ROUTE`), spelt out:
#: the service layer imports this one, not the other way round.
_DAILY_ROUTE = 'licence:daily'


class LicenceClockDB(SQLAlchemySessionManager):
    """The one row of `licence_clock`, and what seeds it."""

    async def database_now(self) -> datetime:
        async with self.session_scope() as session:
            value = (await session.execute(text('SELECT now()'))).scalar_one()
        return _utc(value)

    async def stored(self) -> datetime | None:
        async with self.session_scope() as session:
            value = await session.scalar(select(LicenceClock.seen).where(LicenceClock.id == 1))
        return None if value is None else _utc(value)

    async def seed(self) -> datetime | None:
        """When the newest daily licence record was written, in the deployment's own trail."""
        query = select(func.max(DeploymentAuditEvent.occurred_at)).where(DeploymentAuditEvent.route == _DAILY_ROUTE)
        async with self.session_scope() as session:
            value = await session.scalar(query)
        return None if value is None else _utc(value)

    async def advance(self, seen: datetime) -> datetime:
        row = insert(LicenceClock).values(id=1, seen=seen)
        upsert = row.on_conflict_do_update(
            index_elements=[LicenceClock.id],
            set_={'seen': func.greatest(LicenceClock.seen, row.excluded.seen), 'updated_at': func.now()},
        ).returning(LicenceClock.seen)
        async with self.session_scope() as session:
            value = (await session.execute(upsert)).scalar_one()
        return _utc(value)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
