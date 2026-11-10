"""The two queries the startup checks need.

`app/security/database_checks.py` (then part of
`app/security/startup_checks.py`) ran both itself, which is the invariant
that put it in D — Rule 3 does not exempt a module for not being a
conventional service.

Neither method catches anything. "Could not tell" is a finding the
security check makes, not one the database layer makes for it: the
checks log the driver's own exception type and decide whether a
deployment may proceed on it, and swallowing that here would take the
decision away from the only place that can make it.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel
from sqlalchemy import text

from app.db.sql import SQLAlchemySessionManager
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)


class ConnectionLimits(BaseModel):
    """What the server will allow, as it reports it."""

    max_connections: int
    superuser_reserved: int

    @property
    def usable(self) -> int:
        """Connections available to ordinary clients."""
        return self.max_connections - self.superuser_reserved


class DeploymentChecksDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    """Reads that decide whether this deployment may serve."""

    async def bypass_identity_rows(self, *, user_id: str, org_id: str) -> int:
        """How many of the dev bypass's two rows this database carries.

        Counted in one statement rather than two round trips: the answer
        is only ever "none of it" or "some of it", and one row present is
        as disqualifying as both.
        """
        async with self.session_scope() as session:
            # `int(...)`: `scalar_one` is annotated `Any`, and the sum of
            # two `count(*)`s is the one thing this returns.
            return int(
                (
                    await session.execute(
                        text(
                            'SELECT (SELECT count(*) FROM users WHERE user_id = :u) '
                            '     + (SELECT count(*) FROM organizations WHERE id = :o)'
                        ),
                        {'u': user_id, 'o': org_id},
                    )
                ).scalar_one()
            )

    async def connection_limits(self) -> ConnectionLimits:
        """`max_connections` and the superuser reservation.

        `SHOW`, not a table read: these are server settings, and the
        number that matters is what this server will actually grant.
        """
        async with self.session_scope() as session:
            max_connections = int((await session.execute(text('SHOW max_connections'))).scalar_one())
            reserved = int((await session.execute(text('SHOW superuser_reserved_connections'))).scalar_one())

        return ConnectionLimits(max_connections=max_connections, superuser_reserved=reserved)
