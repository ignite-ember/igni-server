"""What time the database thinks it is.

One query, and it exists as a `*DB` class for the same reason every other
query does: `app/monitoring/clock_skew.py` was reaching past the
persistence layer to run `SELECT now()` itself, which is the one thing
Rule 3 does not allow regardless of how small the statement is.

The reading is deliberately *not* interpreted here. Whether a difference
against this process's clock is tolerable, and what to do about it, is
the monitor's business; this answers only what Postgres said.
"""

from datetime import datetime
import logging

from sqlalchemy import text

from app.db.sql import SQLAlchemySessionManager
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)


class ClockDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    """The database's own clock."""

    async def now(self) -> datetime | None:
        """``SELECT now()``, or None if the database could not be asked.

        Returns None rather than raising because the only caller is a
        metric, and a metric that can take the process down is worse than
        a metric that goes quiet. The caller distinguishes "could not
        measure" from "measured zero" on exactly this None.
        """
        try:
            async with self.session_scope() as session:
                value = (await session.execute(text('SELECT now()'))).scalar()
        except Exception as exc:  # noqa: BLE001 — a metric must not break anything
            logger.debug('Could not read the database clock: %s', exc)
            return None

        return value if isinstance(value, datetime) else None
