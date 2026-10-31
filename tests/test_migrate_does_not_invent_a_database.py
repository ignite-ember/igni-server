"""A wrong POSTGRES_DB must not become a healthy, empty deployment.

``alembic/env.py`` used to connect to the ``postgres`` maintenance
database on every migration, ask whether the target existed, and
``CREATE DATABASE`` it if not. That is wrong in both directions in
production, and both were demonstrated before this test existed.

**It manufactured empty production databases.** ``postgres_db`` defaults
to ``igni``, so an env file that omits ``POSTGRES_DB`` — or misspells it,
or points at a replacement host, or at a volume that was lost — got a
brand new database created and migrated to head. The migrate container
then exits 0, which is precisely the condition
``service_completed_successfully`` makes every other service wait for, so
the whole stack comes up. ``/health`` is deliberately unconditional and
``/ready`` is a ``SELECT 1``, which an empty schema answers, so nothing
anywhere reports a problem. Pointing it at ``igni_prod_typo`` created the
database, ran all 40 revisions and exited 0.

The failure mode is the reason this matters more than its size suggests:
there is no error to act on. An operator sees a green deploy and an empty
product, and the most likely next move — redeploy, restart, roll back —
does nothing, because the configuration is doing exactly what it says.

**And it broke least-privilege deployments.** A role granted only its own
database cannot connect to ``postgres``, so ``alembic upgrade head`` died
before running a single migration even when the target was already at
head — reporting ``permission denied for database "postgres"``, a
database the operator never configured.

These tests need a reachable Postgres and skip without one, since that is
an environment limitation rather than a defect.
"""

from __future__ import annotations

import uuid

from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from alembic import command

from app.config import settings
from tests.conftest import TEST_DB_URL_SYNC


def _server() -> tuple[str, int]:
    """Host and port of the Postgres the rest of the suite uses."""
    hostport = TEST_DB_URL_SYNC.rsplit('@', 1)[1].rsplit('/', 1)[0]
    host, _, port = hostport.partition(':')
    return host, int(port or 5432)


def _admin_engine():
    base = TEST_DB_URL_SYNC.rpartition('/')[0]
    return create_engine(f'{base}/postgres', isolation_level='AUTOCOMMIT')


def _database_exists(name: str) -> bool:
    engine = _admin_engine()
    try:
        with engine.connect() as conn:
            return conn.execute(text('SELECT 1 FROM pg_database WHERE datname = :n'), {'n': name}).scalar() is not None
    finally:
        engine.dispose()


def _drop(name: str) -> None:
    engine = _admin_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    finally:
        engine.dispose()


@pytest.fixture
def postgres_reachable():
    try:
        engine = _admin_engine()
        with engine.connect():
            pass
        engine.dispose()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f'no reachable Postgres here: {type(exc).__name__}: {exc}')


@pytest.fixture
def settings_point_at(monkeypatch, postgres_reachable):
    """Aim ``settings`` at a database name, the way an env file would.

    ``postgres_url_sync`` and ``postgres_default_url`` are plain
    properties over these fields, so setting the fields is enough — this
    exercises the same code path a real deployment takes.
    """
    host, port = _server()
    user, password = TEST_DB_URL_SYNC.split('//', 1)[1].split('@', 1)[0].split(':')

    def aim(*, db: str, app_env: str, password_override: str | None = None):
        monkeypatch.setattr(settings, 'postgres_host', host)
        monkeypatch.setattr(settings, 'postgres_port', port)
        monkeypatch.setattr(settings, 'postgres_user', user)
        monkeypatch.setattr(settings, 'postgres_password', password_override or password)
        monkeypatch.setattr(settings, 'postgres_db', db)
        monkeypatch.setattr(settings, 'app_env', app_env)
        # No ``sqlalchemy.url``, which is what makes env.py take the
        # settings-driven branch the containers use. ``alembic.ini`` leaves
        # it commented out for exactly this reason.
        return Config('alembic.ini')

    return aim


class TestProductionRefusesToInventOne:
    @pytest.mark.parametrize('app_env', ['production', 'prod', 'staging'])
    def test_a_missing_database_stops_the_migration(self, settings_point_at, app_env):
        name = f'igni_absent_{uuid.uuid4().hex[:8]}'
        cfg = settings_point_at(db=name, app_env=app_env)

        with pytest.raises(SystemExit) as raised:
            command.upgrade(cfg, 'head')

        message = str(raised.value)
        assert name in message, 'the operator must be told which database name was wrong'
        assert 'POSTGRES_DB' in message, 'the message must name the setting to fix'
        assert not _database_exists(name), f'{app_env} deploy created "{name}" — a stack that boots green and empty'

    def test_the_exit_is_nonzero(self, settings_point_at):
        """``depends_on: condition: service_completed_successfully`` is the
        whole safety mechanism here: it gates every other service in the
        compose file on this process exiting 0 — asserted as a rule in
        `tests/test_compose_deployment.py`. A refusal that exited 0 would
        let them all start anyway, against a schema that is not there."""
        name = f'igni_absent_{uuid.uuid4().hex[:8]}'
        cfg = settings_point_at(db=name, app_env='production')

        with pytest.raises(SystemExit) as raised:
            command.upgrade(cfg, 'head')

        # ``SystemExit(str)`` exits 1 and prints the string; the assertion
        # is that the code is not 0 or None, either of which would read as
        # success to compose.
        assert raised.value.code not in (0, None), raised.value.code


class TestDevelopmentIsStillConvenient:
    """The auto-create is a genuine local affordance — ``make up`` on a
    fresh checkout should not require a manual ``createdb``. Keeping it
    for dev is why this is a narrowing rather than a removal."""

    def test_dev_creates_and_migrates_it(self, settings_point_at):
        name = f'igni_devfresh_{uuid.uuid4().hex[:8]}'
        cfg = settings_point_at(db=name, app_env='dev')
        try:
            command.upgrade(cfg, 'head')

            assert _database_exists(name)
            engine = create_engine(f'{TEST_DB_URL_SYNC.rpartition("/")[0]}/{name}')
            try:
                with engine.connect() as conn:
                    assert conn.execute(text('SELECT count(*) FROM users')).scalar() == 0
            finally:
                engine.dispose()
        finally:
            _drop(name)


class TestTheMaintenanceDatabaseIsLeftAlone:
    def test_an_existing_database_migrates_without_touching_postgres(self, settings_point_at, monkeypatch):
        """The least-privilege case, expressed without needing a role.

        A deployment whose Postgres role is granted only its own database
        cannot connect to ``postgres`` at all. Rather than create such a
        role, this points ``postgres_default_url`` somewhere that cannot
        possibly answer: if the migration succeeds anyway, the maintenance
        database was never contacted.

        Before the fix this raised ``OperationalError`` on that unreachable
        URL — proving the old code went there unconditionally, which is
        exactly why a restricted role could not deploy.
        """
        unreachable = 'postgresql+psycopg2://nobody:nobody@127.0.0.1:1/postgres'
        cfg = settings_point_at(db=TEST_DB_URL_SYNC.rpartition('/')[2], app_env='production')
        monkeypatch.setattr(type(settings), 'postgres_default_url', property(lambda _: unreachable))

        # The suite's own database exists and conftest already migrated it,
        # so this is the everyday redeploy: nothing to do, and it must not
        # need any privilege beyond the app database.
        command.upgrade(cfg, 'head')


class TestRealErrorsAreNotMasked:
    def test_a_wrong_password_reports_the_password(self, settings_point_at):
        """Going to the maintenance database on *any* failure replaced a
        precise error with a vaguer one about a database the operator never
        configured. A bad password must still say so."""
        cfg = settings_point_at(
            db=TEST_DB_URL_SYNC.rpartition('/')[2],
            app_env='production',
            password_override='definitely-not-the-password',
        )

        with pytest.raises(OperationalError) as raised:
            command.upgrade(cfg, 'head')

        message = str(raised.value)
        assert 'password authentication failed' in message, message
        assert 'permission denied for database "postgres"' not in message
