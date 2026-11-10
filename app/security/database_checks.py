"""The startup checks that need the database.

Separate from :class:`app.security.startup_checks.SettingsChecks`
because those deliberately touch nothing — they run before anything is
known to be reachable.

A database that cannot be queried is not a finding: that is what
``/ready`` is for, and failing startup on a transient connection error
would turn a blip into an outage. So a driver or connection error is
logged as "skipped, not passed" and reports nothing. Anything else is a
bug in the check and is left to propagate.
"""

from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings, settings as app_settings
from app.db.sql.deployment_checks import DeploymentChecksDB
from app.security.bypass_identity import BYPASS_ORG_ID, BYPASS_USER_ID
from app.security.deployment_findings import DeploymentFindings, UnsafeDeployment

logger = logging.getLogger(__name__)

#: What "the database could not be asked" looks like: SQLAlchemy's own
#: and the DBAPI errors it wraps, and the socket-level failures
#: (refused, reset, timed out) that reach us unwrapped from the driver.
_UNREACHABLE = (SQLAlchemyError, OSError)


class DatabaseChecks:
    """Reads that decide whether this deployment may serve, and how it is sized."""

    def __init__(self, *, settings: Settings = app_settings, db: DeploymentChecksDB | None = None) -> None:
        self._settings = settings
        self._db = db or DeploymentChecksDB()

    async def check_no_bypass_identity_in_database(self, *, raise_on_unsafe: bool = True) -> list[str]:
        """Refuse to serve a database carrying the dev bypass's admin account.

        ``PORTAL_AUTH_BYPASS`` is refused outside dev, so the bypass cannot
        *operate* in production. But it does not only skip authentication
        — it writes rows. ``portal_auth.get_current_portal_user`` calls
        ``ensure_exists`` for a user and an organisation, so any database a
        bypass-enabled server has ever touched carries an administrator
        (``BYPASS_USER_ID`` with an empty ``hashed_password``) owning
        ``BYPASS_ORG_ID``. Those rows outlive the flag: a local database
        that is later promoted, restored into production or reused ships a
        real administrator account.

        Demonstrated end to end against ``APP_ENV=production`` and
        ``PORTAL_AUTH_BYPASS=false``: a sign-in code requested for the
        bypass address, redeemed, gave ``role: admin`` on the demo org. The
        one-time code is the only thing in the way, and with
        ``LOG_SIGN_IN_CODES=true`` — the documented first-setup
        configuration with no relay — it is in the server log.

        So this refuses to start rather than warning. A warning is the
        wrong shape for a live administrator account: it scrolls past, and
        the deployment keeps serving.
        """
        if self._settings.app_env.lower() == 'dev':
            return []

        try:
            found = await self._db.bypass_identity_rows(user_id=BYPASS_USER_ID, org_id=BYPASS_ORG_ID)
        except _UNREACHABLE as exc:
            logger.warning(
                'Could not check for the dev bypass identity (%s: %s). This check is skipped, not passed.',
                type(exc).__name__,
                exc,
            )
            return []

        if not found:
            return []

        message = DeploymentFindings.explain(
            f'This database contains the local-development bypass identity '
            f'({BYPASS_USER_ID!r} / {BYPASS_ORG_ID!r}) and APP_ENV={self._settings.app_env!r}. '
            f'That is a real administrator account with an empty password, created by '
            f'PORTAL_AUTH_BYPASS on some machine that pointed at this database. Anyone '
            f'who can read a one-time code — from the server log when LOG_SIGN_IN_CODES is on, '
            f'or from its mailbox — can sign in as an admin.',
            # Text for the operator to run by hand, never executed here (B608).
            'remove both rows before serving:\n'  # nosec B608
            f"           DELETE FROM organizations WHERE id = '{BYPASS_ORG_ID}';\n"
            f"           DELETE FROM users WHERE user_id = '{BYPASS_USER_ID}';\n"
            '         If this *is* a development database, set APP_ENV=dev.',
        )

        logger.error('Deployment check: %s', message)
        if raise_on_unsafe:
            raise UnsafeDeployment('Refusing to start:\n\n' + message)
        return [message]

    async def check_pool_fits_the_server(self) -> list[str]:
        """Say whether this process's pool can fit alongside its siblings.

        A **warning**, never a refusal. The arithmetic depends on how many
        replicas the operator runs, which this process cannot know, so an
        exact answer is not available and refusing on a guess would stop a
        deployment that is fine. What it *can* do is state both numbers next
        to each other, which is the thing nobody has in front of them when
        they choose an instance size.

        ``pool_size + max_overflow`` is one process's ceiling. Multiply by the
        API replicas, add the worker, add the migration job and the backup and
        whatever psql session an operator has open. Cloud SQL's smallest tier
        allows 25 connections in total; the shipped defaults are 20 per
        process. That combination works until the second replica starts, and
        then fails as load arrives rather than at deploy time — which is the
        worst moment to learn it. F116.
        """
        pool_size = self._settings.db_pool_size
        max_overflow = self._settings.db_max_overflow
        ceiling = pool_size + max_overflow

        try:
            limits = await self._db.connection_limits()
        except _UNREACHABLE as exc:
            logger.warning(
                'Could not read the database server max_connections (%s). This process will '
                'use up to %d connections; check that the server allows for it across every replica.',
                type(exc).__name__,
                ceiling,
            )
            return []

        usable = limits.usable
        logger.info(
            'Connection budget: this process may use %d (pool %d + overflow %d); '
            'the server allows %d (max_connections %d less %d reserved).',
            ceiling,
            pool_size,
            max_overflow,
            usable,
            limits.max_connections,
            limits.superuser_reserved,
        )

        # Two processes' worth is the smallest shape anything we ship
        # deploys — the chart's default is two API replicas, and even the
        # single-instance compose stack runs an API and a worker.
        if ceiling * 2 <= usable:
            return []

        problem = (
            f'DB_POOL_SIZE + DB_MAX_OVERFLOW is {ceiling}, and the database server allows '
            f'{usable} connections in total. Two processes at that size would exhaust it, '
            f'and everything we ship runs at least two — the chart defaults to two API '
            f'replicas plus a worker. Postgres answers "too many clients already" and '
            f'refuses the connection, which surfaces as 500s from whichever replica '
            f'started second. Either lower DB_POOL_SIZE/DB_MAX_OVERFLOW, or raise the '
            f"server's max_connections (a managed instance may need a larger tier: Cloud "
            f"SQL's smallest allows 25)."
        )
        logger.warning('Deployment check: %s', problem)
        return [problem]
