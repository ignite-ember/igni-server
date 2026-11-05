"""Shared fixtures for portal integration tests.

Uses a real 'ember_test' PostgreSQL database. Tables are created once
at import. Each test gets a clean slate via TRUNCATE. The app's own
singleton DB managers connect to ember_test via patched settings.
"""

from datetime import datetime, timezone
import os
import uuid

# Before anything imports `app.config`: the suite reads the environment, never
# the checkout's `.env` (see `_DOTENV` there).
os.environ['IGNI_IGNORE_DOTENV'] = '1'

import bcrypt
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession, create_async_engine

from app.db.sql.models import Base, load_all_models
from app.db.sql.models.org_domain import OrgDomain
from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.models.organization import Organization
from app.db.sql.models.user import User
from app.db.sql.tenancy import TenantSessions
from app.services.auth.jwt_service import JWTService
from app.services.identity.key_ring import KeyRing, SigningKeys
from tests.egress_guard import EgressGuard, InternalNetwork

#: `IGNI_TEST_PG_PORT` moves the suite off 5432 so it can run beside another
#: Postgres (a sibling repo's suite, a dev stack). CI leaves it unset.
_TEST_PG_PORT = os.environ.get('IGNI_TEST_PG_PORT', '5432')
#: The URLs stay plain literals with 5432 in them: `test_lint_is_the_same_locally_and_in_ci`
#: reads the database name and credentials out of this file and compares them with CI's.
#: 127.0.0.1, not `localhost`: see `test_the_suite_connects_to_127_0_0_1`.
TEST_DB_URL_ASYNC = 'postgresql+asyncpg://myuser:mysecretpassword@127.0.0.1:5432/ember_test'.replace(
    ':5432/', f':{_TEST_PG_PORT}/'
)
TEST_DB_URL_SYNC = 'postgresql+psycopg2://myuser:mysecretpassword@127.0.0.1:5432/ember_test'.replace(
    ':5432/', f':{_TEST_PG_PORT}/'
)

#: Each organisation's own database is `ember_test_t<n>`, copied from the
#: template (`app.db.sql.tenancy`). Matched with LIKE, so `_` is escaped.
_TENANT_DATABASES = r'ember\_test\_t%'
_TENANT_TEMPLATE = 'ember_test_tenant_template'

# Load all models so Base.metadata has every table
load_all_models()

# Create tables synchronously at import time (runs once per test session)
_setup_done = False

# Held for the whole session so a second pytest — or anything else pointed
# at ``ember_test`` — is refused instead of interleaving with it.
_db_lock_connection = None

# Arbitrary, stable, and specific to this database.
_TEST_DB_LOCK_KEY = 0x1911_1_7357

# Process-scoped marker; see the note in ``_claim_the_test_database``.
_LOCK_MARKER = '_IGNI_TEST_DB_LOCK_PID'


def _claim_the_test_database() -> None:
    """Refuse to start if something else is already using ``ember_test``.

    This suite drops and rebuilds ``public`` at import and truncates tables
    between tests, so two things using the database at once corrupt each
    other's state. The symptom is not a clear error — it is a scattered
    handful of unrelated failures that look exactly like a regression.

    That happened three times in one sitting during the production review:
    a live uvicorn pointed at ``ember_test`` for a webhook probe produced
    "5 failed, 1 error" in ``test_portal_groups``; a `ruff --fix` running
    alongside a full suite produced a phantom failure in a test that passes
    alone; and a second full run overlapping the first produced ten. Each
    cost time to prove innocent, and each *looked* like the change under
    test had broken something.

    A Postgres advisory lock is the right shape for this: it is held by the
    connection, so it releases on its own if a run crashes, and there are no
    stale locks to clean up. The alternative — a per-run database name —
    would isolate concurrent runs properly but changes ``TEST_DB_URL_SYNC``,
    which several modules import directly.
    """
    global _db_lock_connection
    if _db_lock_connection is not None:
        return

    # The marker lives in the environment, not in a module global, because
    # this file is imported twice under two identities: pytest loads it as
    # ``conftest`` and every test module then does
    # ``from tests.conftest import ...``, which executes it again as
    # ``tests.conftest``. Two module objects means two sets of globals, so a
    # module-level flag guards nothing — and the second claim came from the
    # same process on a different connection, which Postgres correctly
    # refuses. The first version of this lock refused the whole suite for
    # exactly that reason.
    import os

    if os.environ.get(_LOCK_MARKER) == str(os.getpid()):
        return

    from sqlalchemy import create_engine, text as _sql_text

    engine = create_engine(TEST_DB_URL_SYNC)
    conn = engine.connect()
    got = conn.execute(_sql_text('SELECT pg_try_advisory_lock(:k)'), {'k': _TEST_DB_LOCK_KEY}).scalar()
    if not got:
        conn.close()
        engine.dispose()
        import pytest as _pytest

        _pytest.exit(
            'Another process is already using the ember_test database.\n'
            '  This suite rebuilds the schema and truncates tables, so running two at '
            'once produces unrelated-looking failures rather than an error.\n'
            '  Finish or stop the other run — a second pytest, or a dev server pointed '
            'at ember_test — and try again.',
            returncode=1,
        )
    # The advisory lock only stops another *pytest* — nothing else claims
    # it. The case that caused this in the first place was a live uvicorn
    # pointed at ember_test for a webhook probe, and a server does not take
    # locks, so it would still interleave.
    #
    # So also count who else is connected. This runs before conftest opens
    # any other engine, so the only backend belonging to this process is the
    # one holding the lock; anything beyond it is somebody else.
    others = conn.execute(
        _sql_text(
            'SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()'
        )
    ).scalar_one()
    if others:
        detail = conn.execute(
            _sql_text(
                "SELECT string_agg(DISTINCT coalesce(nullif(application_name, ''), 'unknown'), ', ') "
                'FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()'
            )
        ).scalar()
        conn.close()
        engine.dispose()
        import pytest as _pytest

        _pytest.exit(
            f'{others} other connection(s) to the ember_test database '
            f'(application_name: {detail}).\n'
            '  This suite rebuilds the schema and truncates tables, so a server or '
            'shell holding it open will corrupt the run — and the symptom is '
            'unrelated-looking failures rather than an error.\n'
            '  Stop whatever is connected. If you need a live server against real '
            'data, give it its own database rather than the test one.',
            returncode=1,
        )

    # Kept alive deliberately: closing either the connection or the engine
    # would release the lock.
    _db_lock_connection = (engine, conn)
    os.environ[_LOCK_MARKER] = str(os.getpid())


def _ensure_tables():
    """Build the test DB the same way prod builds it — via alembic.

    Previously this called ``Base.metadata.create_all`` which builds
    the schema from whatever the models currently say. Dev/stage/prod
    build from migrations, so any model addition without a matching
    alembic file silently passed tests but blew up at runtime in dev.
    Running ``alembic upgrade head`` here gives tests the exact same
    schema as production.
    """
    global _setup_done
    if _setup_done:
        return
    _setup_done = True

    _claim_the_test_database()

    from alembic.config import Config
    from sqlalchemy import create_engine, text

    from alembic import command

    # ``DROP SCHEMA public CASCADE`` instead of ``Base.metadata.drop_all``
    # because the latter leaves alembic's own ``alembic_version`` table
    # behind. Stale ``alembic_version`` rows pointing at deleted
    # revisions make the subsequent ``upgrade head`` fail with
    # "Can't locate revision X" — which happened the first time the
    # squash migration landed and the old per-revision rows were still
    # referenced from a previous test run.
    engine = create_engine(TEST_DB_URL_SYNC)
    with engine.connect() as conn:
        conn.execute(text('DROP SCHEMA public CASCADE'))
        conn.execute(text('CREATE SCHEMA public'))
        conn.commit()
    engine.dispose()

    cfg = Config('alembic.ini')
    cfg.set_main_option('sqlalchemy.url', TEST_DB_URL_SYNC)
    command.upgrade(cfg, 'head')

    # Organisation databases from an earlier run, and the template too, so
    # it is rebuilt from the tenant migrations as they are now — the same
    # reason the shared schema is dropped above.
    _drop_tenant_databases(include_template=True)


def _drop_tenant_databases(*, include_template: bool = False) -> None:
    """Drop every organisation database. `WITH (FORCE)` ends stray connections."""
    from sqlalchemy import create_engine, text

    engine = create_engine(TEST_DB_URL_SYNC, isolation_level='AUTOCOMMIT')
    with engine.connect() as conn:
        names = (
            conn.execute(text('SELECT datname FROM pg_database WHERE datname LIKE :p'), {'p': _TENANT_DATABASES})
            .scalars()
            .all()
        )
        for name in names:
            if name == _TENANT_TEMPLATE and not include_template:
                continue
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    engine.dispose()


_ensure_tables()


# ── JWT helper ────────────────────────────────────────────────────────

#: One key for the whole run. Generating RSA keys is not free, and no
#: test needs a different one — the ones about keys build their own.
TEST_RING = KeyRing.generated()
SigningKeys.install(TEST_RING)
_jwt = JWTService()


def the_app():
    """The application, imported without leaving `APP_ENV` behind.

    Five test files needed `app.main` for its routes or its OpenAPI
    schema, and each did `os.environ.setdefault('APP_ENV', 'dev')` first
    because importing it without one refuses. None of them put it back.

    That makes an unrelated test's result depend on ordering.
    `test_a_deployment_outside_google_cloud_starts` builds
    `Settings(_env_file=dist.env)` and asserts the template ships
    `APP_ENV=prod` — and environment variables beat an env file in
    pydantic-settings, so a leaked `dev` makes a correct template look
    wrong.

    It has never fired, and only by luck: all five leakers sort after
    `test_a_deployment_…` alphabetically, and the suite runs in
    collection order. What surfaced it was reading the schema at
    **collection** time for a `parametrize`, which happens before any
    test runs and so beats the ordering entirely. A `-k` subset, a
    shard, or a rename would do the same.

    So: set it if absent, restore exactly what was there, and hand back
    the app.
    """
    import os

    previous = os.environ.get('APP_ENV')
    os.environ.setdefault('APP_ENV', 'dev')
    try:
        from app.main import app

        return app
    finally:
        if previous is None:
            os.environ.pop('APP_ENV', None)
        else:
            os.environ['APP_ENV'] = previous


def make_token(user_id: str, org_id: str | None = None, org_name: str | None = None) -> str:
    return _jwt.create_token(user_id, org_id=org_id, org_name=org_name)


# ── Nothing leaves the network, in any test ──────────────────────────


@pytest.fixture(autouse=True)
def no_egress(request: pytest.FixtureRequest):
    """Every test runs as if inside an air-gapped network.

    A connection to anything outside the deployment is refused the way a
    network with no route refuses it, and recorded. The test then fails
    at teardown with the destination named, even when the code under
    test caught the error and carried on — which is what code built to
    tolerate being offline does, and why refusing alone proves nothing.

    `tests/test_igni_reaches_nothing_outside_the_network.py` is the
    deliberate version: it boots the app and drives it. This fixture is
    the net under everything else. See `tests/egress_guard.py` for what
    counts as inside and what the guard cannot see.

    `@pytest.mark.reaches_outside_on_purpose` is for the guard's own
    tests, which have to make an attempt to prove one is seen. The guard
    still refuses inside them; only this assertion stands aside.
    """
    with EgressGuard(network=InternalNetwork()).watching() as log:
        yield log
    if request.node.get_closest_marker('reaches_outside_on_purpose') is None:
        assert not log.attempts, log.report()


# ── Patch settings to use test DB and test JWT ────────────────────────


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch):
    """Redirect all DB singletons to ember_test and use test JWT secret."""
    from app.config import Settings

    # Point postgres_url to test DB so any new engine created uses ember_test
    monkeypatch.setattr(Settings, 'postgres_url', property(lambda self: TEST_DB_URL_ASYNC))

    # The run's signing keys, in case a test replaced them.
    SigningKeys.install(TEST_RING)

    # Ensure portal_auth_bypass is off
    from app.config import settings

    monkeypatch.setattr(settings, 'portal_auth_bypass', False)

    # Disable email sending in tests to prevent BackgroundTasks from hitting SMTP
    monkeypatch.setattr(settings, 'email_enabled', False)

    # Disable public beta guards so billing/org tests can exercise full flows
    monkeypatch.setattr(settings, 'public_beta', False)

    # Force the next access to rebuild the engine against the test DB URL.
    # One engine per event-loop id, shared by every repository (F116), so
    # clearing the class-level registry is the whole job — it used to be
    # a loop over every singleton instance because each subclass had its
    # own.
    from app.db.sql import SQLAlchemySessionManager

    SQLAlchemySessionManager._engines.clear()


# ── Per-test DB session (for test data setup) ─────────────────────────


@pytest.fixture
async def db():
    """Provide an async session connected to ember_test for test data setup."""
    engine = create_async_engine(TEST_DB_URL_ASYNC, echo=False)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_tables():
    """Truncate all tables and reset singleton engines after each test."""
    yield
    # Properly dispose singleton engines so asyncpg connections are closed
    # while the event loop is still running.
    from app.db.sql import SQLAlchemySessionManager

    await SQLAlchemySessionManager.dispose_all()
    await TenantSessions().dispose()

    # Each organisation's own database goes, not just its rows: the
    # registry in the shared plane is truncated below, so the next test's
    # first organisation is number 1 again and must not find number 1's
    # database still there.
    _drop_tenant_databases()

    engine = create_async_engine(TEST_DB_URL_ASYNC, echo=False)
    async with engine.begin() as conn:
        table_names = ', '.join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
        if table_names:
            await conn.execute(text(f'TRUNCATE {table_names} CASCADE'))
        # TRUNCATE fires no row trigger, so the large objects behind
        # extension assets would outlive their rows from test to test.
        await conn.execute(text('SELECT lo_unlink(oid) FROM pg_largeobject_metadata'))
    await engine.dispose()


@pytest.fixture
def org_db():
    """A session on one organisation's own database, for test data and checks.

    `async with org_db(org_id) as session:` — committed on success, like
    the application's own. The organisation's database is created on
    first use, as it is in the application.
    """
    return TenantSessions().session_scope


@pytest.fixture
async def verify_db():
    """Provide a fresh session for verification queries after app code has modified the DB.

    Use this instead of the `db` fixture when you need to read rows that were
    committed by the app's singleton DB managers (e.g. TeamDB, UserDB), since
    the `db` fixture's session will have stale objects in its identity map.
    """
    engine = create_async_engine(TEST_DB_URL_ASYNC, echo=False)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ── Data factories ────────────────────────────────────────────────────


async def create_user(
    db: AsyncSession,
    user_id: str | None = None,
    email: str = 'test@example.com',
    full_name: str = 'Test User',
    auth_provider: str = 'email',
    is_super_admin: bool = False,
) -> User:
    now = datetime.now(timezone.utc)
    user = User(
        id=str(uuid.uuid4()),
        user_id=user_id or str(uuid.uuid4()),
        email=email,
        hashed_password='$2b$12$fakehash',
        full_name=full_name,
        auth_provider=auth_provider,
        is_super_admin=is_super_admin,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    await db.commit()
    return user


async def create_org(
    db: AsyncSession,
    owner_user_id: str,
    slug: str = 'test-org',
    display_name: str = 'Test Org',
    org_id: str | None = None,
    domains: tuple[str, ...] = (),
) -> Organization:
    now = datetime.now(timezone.utc)
    oid = org_id or str(uuid.uuid4())
    org = Organization(
        id=oid,
        slug=slug,
        display_name=display_name,
        owner_user_id=owner_user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(org)
    # The owner joins it unless they are a member elsewhere already: a
    # person is in one organisation at a time (migration 0022), so a
    # second organisation of the same owner is owned, not joined.
    elsewhere = (
        await db.execute(
            select(OrgMembership.id).where(
                OrgMembership.user_id == owner_user_id, OrgMembership.status.in_(('active', 'deactivated'))
            )
        )
    ).first()
    if elsewhere is None:
        db.add(
            OrgMembership(
                id=str(uuid.uuid4()),
                org_id=oid,
                user_id=owner_user_id,
                role='admin',
                status='active',
                created_at=now,
                updated_at=now,
            )
        )
    # The organisation first: nothing tells the flush to order the two
    # tables, and the domain's foreign key needs the row there.
    await db.flush()
    for domain in domains:
        db.add(OrgDomain(id=str(uuid.uuid4()), org_id=oid, domain=domain, created_at=now))
    await db.commit()
    return org


async def add_member(
    db: AsyncSession,
    org_id: str,
    user_id: str,
    role: str = 'member',
    status: str = 'active',
) -> OrgMembership:
    """Ensure ``user_id`` is a member of ``org_id`` with ``role``.

    Idempotent on purpose. ``create_org`` already inserts its owner as an
    admin, so a test that spells that out — ``create_org(owner_user_id='u1')``
    followed by ``add_member(..., 'u1', role='admin')`` — used to hit the
    ``idx_org_memberships_unique`` constraint and fail during setup. Being
    explicit about the owner's role reads better than relying on the fixture's
    side effect, so the helper accommodates it instead of forbidding it.
    """
    existing = (
        (
            await db.execute(
                select(OrgMembership).where(
                    OrgMembership.org_id == org_id,
                    OrgMembership.user_id == user_id,
                )
            )
        )
        .scalars()
        .first()
    )

    now = datetime.now(timezone.utc)
    if existing is not None:
        existing.role = role
        existing.status = status
        existing.updated_at = now
        await db.commit()
        return existing

    mem = OrgMembership(
        id=str(uuid.uuid4()),
        org_id=org_id,
        user_id=user_id,
        role=role,
        status=status,
        created_at=now,
        updated_at=now,
    )
    db.add(mem)
    await db.commit()
    return mem


async def put_in_org(org_id: str, *rows) -> None:
    """Rows into an organisation's own database.

    The per-organisation tables are not in `ember_test`, so a fixture that
    would once have `db.add(...)`-ed a group or a notification writes here
    instead. In order, so a child row is not inserted before its parent.
    """
    async with TenantSessions().session_scope(org_id) as session:
        for row in rows:
            session.add(row)
            await session.flush()


def audit_rows(**where) -> list[dict]:
    """Audit rows from every trail — the deployment's and each organisation's.

    A privileged action is recorded in its organisation's own database
    and an action with no organisation in the shared plane. A test asking
    "was this recorded" should not have to know which, so this reads them
    all, newest first. Synchronous, for the reason `test_audit_trail.py`
    gives: the `db` and `client` fixtures run on different loops.
    """
    clauses = ' AND '.join(f'{k} = :{k}' for k in where) or 'true'
    sql = f'SELECT * FROM audit_events WHERE {clauses}'
    base, _, _ = TEST_DB_URL_SYNC.rpartition('/')
    shared = create_engine(TEST_DB_URL_SYNC)
    try:
        with shared.connect() as conn:
            rows = [dict(r) for r in conn.execute(text(sql), where).mappings().all()]
            databases = [str(d) for d in conn.execute(text('SELECT database FROM tenants')).scalars().all()]
    finally:
        shared.dispose()
    for database in databases:
        engine = create_engine(f'{base}/{database}')
        try:
            with engine.connect() as conn:
                rows += [dict(r) for r in conn.execute(text(sql), where).mappings().all()]
        finally:
            engine.dispose()
    return sorted(rows, key=lambda r: r['occurred_at'], reverse=True)


# ── HTTP client ───────────────────────────────────────────────────────


@pytest.fixture
def app():
    from app.main import app as _app

    return _app


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url='http://test/v1',
    ) as c:
        yield c


def auth_header(token: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture
def email_relay(monkeypatch):
    """Make outbound email look configured, without sending any.

    The suite disables email globally so nothing reaches a real relay.
    That default is also a real deployment shape — no SMTP at all — so
    tests that mean to exercise the *delivered* path have to say so, and
    this is how they say it.
    """
    from app.config import settings
    from app.services.email.service import EmailService

    monkeypatch.setattr(settings, 'email_enabled', True)
    monkeypatch.setattr(settings, 'smtp_host', 'smtp.test.invalid')

    sent: list[dict] = []

    def _record(name):
        async def _send(*args, **kwargs):
            sent.append({'method': name, **kwargs})
            return True

        return _send

    for method in (
        'send_welcome',
        'send_otp_code',
        'send_sign_up_code',
        'send_password_reset_code',
        'send_team_invite',
    ):
        monkeypatch.setattr(EmailService, method, _record(method))
    return sent


async def create_user_with_password(
    db: AsyncSession,
    password: str,
    user_id: str | None = None,
    email: str = 'test@example.com',
    full_name: str = 'Test User',
    auth_provider: str = 'email',
) -> User:
    """Create a user with a real bcrypt-hashed password (for sign-in tests)."""
    now = datetime.now(timezone.utc)
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(
        id=str(uuid.uuid4()),
        user_id=user_id or str(uuid.uuid4()),
        email=email,
        hashed_password=hashed,
        full_name=full_name,
        auth_provider=auth_provider,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    await db.commit()
    return user


async def add_invited_member(
    db: AsyncSession,
    org_id: str,
    email: str,
    role: str = 'member',
    invited_by: str | None = None,
) -> OrgMembership:
    """Create an OrgMembership with status='invited' and a placeholder user_id."""
    now = datetime.now(timezone.utc)
    mem = OrgMembership(
        id=str(uuid.uuid4()),
        org_id=org_id,
        user_id=f'invited-{uuid.uuid4().hex[:12]}',
        invited_email=email,
        role=role,
        status='invited',
        invited_by=invited_by,
        created_at=now,
        updated_at=now,
    )
    db.add(mem)
    await db.commit()
    return mem
