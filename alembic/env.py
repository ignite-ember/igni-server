from logging.config import fileConfig

from sqlalchemy import create_engine, engine_from_config, pool, text
from sqlalchemy.exc import OperationalError

from alembic import context

from app.config import settings
from app.db.sql.models import Base, load_all_models

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
#
# ``disable_existing_loggers=False`` is the important part, and it is not
# the default. ``fileConfig`` otherwise sets ``.disabled = True`` on every
# logger that the config file does not name — every ``app.*`` logger in
# this project — and a disabled logger emits nothing at all.
#
# That is the cause of a mystery this repository had worked around twice.
# ``tests/test_webhook_failures_are_client_errors.py`` and igni's
# ``tests/test_group_policy_path_containment.py`` both say that something
# in the test session sets ``logger.disabled``, that the cause "is not
# identified", and both clear the flag by hand to get their assertions to
# run. This line was it: ``conftest`` builds the schema with
# ``alembic upgrade head``, that imports this module, and every logger
# created before it goes quiet for the rest of the process.
#
# Demonstrated rather than assumed:
#
#     >>> logging.getLogger('app.services.email.transports.ses').disabled
#     False
#     >>> fileConfig('alembic.ini')
#     >>> logging.getLogger('app.services.email.transports.ses').disabled
#     True
#
# Harmless in the migrate container, which runs once and exits. Not
# harmless anywhere that keeps serving afterwards, or in a test session.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata

load_all_models()
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    # Prefer an explicit ``sqlalchemy.url`` supplied by the caller
    # (drift-check script, tests, ad-hoc one-off DBs) — falling back
    # to ``settings.postgres_url_sync`` keeps the prod / docker
    # ``alembic upgrade head`` path unchanged.
    url = config.get_main_option('sqlalchemy.url') or settings.postgres_url_sync
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
    )

    print('Running the migrations offline')
    with context.begin_transaction():
        context.run_migrations()
    print('Done')


def _database_is_missing(exc: OperationalError) -> bool:
    """True when Postgres refused because the database does not exist.

    Matched on the message text rather than ``pgcode``, because psycopg2
    leaves ``pgcode`` as ``None`` on every *connection* failure — checked
    against a missing database, a wrong password, a revoked CONNECT and a
    dead host, and all four gave ``None``.

    The message is distinctive, and the four cases are distinguishable:

        FATAL:  database "igni" does not exist
        FATAL:  password authentication failed for user "igni"
        FATAL:  permission denied for database "postgres"
        Connection refused

    Deliberately conservative — it requires the database name as well, so
    a reworded or localised message falls through to "some other error"
    and gets re-raised unchanged. That is the safe direction to be wrong
    in: the cost is a confusing dev failure, not a manufactured database.
    """
    message = str(exc.orig)
    return f'database "{settings.postgres_db}" does not exist' in message


def ensure_target_database() -> None:
    """Make sure the target database is there — and refuse to invent one.

    This used to connect to the ``postgres`` maintenance database
    unconditionally, ask ``pg_database`` whether the target existed, and
    ``CREATE DATABASE`` it if not. Convenient locally, and wrong in both
    directions in production:

    **It manufactured empty production databases.** ``postgres_db``
    defaults to ``igni``, so an env file that omits ``POSTGRES_DB`` — or
    spells it wrong, or points at a replacement host, or at a lost
    volume — got a brand new empty database created and migrated to head.
    The migrate container then exits 0, which is the condition every
    other service waits on, so the whole stack comes up. ``/health`` and
    ``/ready`` both pass, because ``/ready`` is a ``SELECT 1`` and an
    empty schema answers it fine. Demonstrated: pointing this at
    ``igni_prod_typo`` created the database, ran all 40 revisions and
    exited 0, leaving a deployment that is indistinguishable from a
    healthy one except that every user's data is gone. There is no
    recovery step here that a human gets to make a decision about,
    because nothing tells them a decision exists.

    **And it broke least-privilege deployments.** A role granted only its
    own database cannot connect to ``postgres`` at all, so
    ``alembic upgrade head`` died before running a single migration —
    even when the target database existed and was already at head. The
    error names ``postgres``, not the app database, so it reads like a
    misconfiguration somewhere else entirely. Verified against a role
    with no CONNECT on ``postgres``: ``permission denied for database
    "postgres"``.

    So: connect to the *target* first. That is the normal case, it
    succeeds, and the maintenance database is never touched. Only a
    genuinely missing database asks the question — and outside dev the
    answer is no.
    """
    engine = create_engine(settings.postgres_url_sync, poolclass=pool.NullPool)
    try:
        with engine.connect():
            return
    except OperationalError as exc:
        if not _database_is_missing(exc):
            # Wrong password, unreachable host, revoked CONNECT. Going to
            # the maintenance database next would replace a precise error
            # with a vaguer one about a database the operator never
            # configured.
            raise
    finally:
        engine.dispose()

    name = settings.postgres_db

    if settings.app_env.lower() != 'dev':
        raise SystemExit(
            f'\nRefusing to migrate: database "{name}" does not exist on '
            f'{settings.postgres_host}:{settings.postgres_port}.\n\n'
            'Creating it would give you a deployment that starts cleanly, passes '
            'every health check and contains nothing — which is what happens when '
            'POSTGRES_DB is unset (it defaults to "igni"), misspelled, or pointed '
            'at a new host or an empty volume.\n\n'
            f'  Fix: if this really is a first install, create it once by hand:\n'
            f'         createdb {name}\n'
            '       otherwise correct POSTGRES_DB / POSTGRES_HOST and deploy again.\n'
        )

    print(f'Database "{name}" does not exist. Creating it (APP_ENV=dev)...')
    admin = create_engine(settings.postgres_default_url, isolation_level='AUTOCOMMIT')
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()
    print(f'Database "{name}" created successfully.')


def _already_in_the_database(object_, name, type_, reflected, compare_to):
    """Keep autogenerate from re-offering indexes it cannot see.

    A partial index — one with ``postgresql_where`` — is not reliably
    reflected, so alembic compares the model's against nothing and
    reports it as *added* on every single run.
    ``uq_org_memberships_pending_invite`` was offered by three
    consecutive autogenerates. Each one had to be edited by hand, and
    each time the thing being deleted was a `create_index` for an
    index that already exists plus a `drop_index` in the downgrade
    that would have removed a real one.

    Three hand-edits is where a recurring manual step becomes a bug
    waiting for whoever does not know to make it. Named individually
    rather than skipping partial indexes as a class: a *genuinely* new
    partial index should still be offered, and silence there would be
    the worse failure.

    The argument order is alembic's and is easy to get backwards —
    ``(object, name, ...)``, not ``(name, object, ...)``. Reversed,
    this filters nothing and fails open, which looks exactly like
    working until the next autogenerate offers the index again.
    """
    return not (type_ == 'index' and name in _REFLECTION_BLIND_SPOTS)


#: Indexes a migration already creates and autogenerate cannot see.
#: Each entry names the migration that owns it, so the claim can be
#: checked rather than trusted.
_REFLECTION_BLIND_SPOTS = {
    # Created by `9f2b7c41ad86_one_pending_invitation_per_address`.
    'uq_org_memberships_pending_invite',
}


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # Prefer an explicit ``sqlalchemy.url`` supplied by the caller —
    # see the offline branch for the rationale.
    url = config.get_main_option('sqlalchemy.url') or settings.postgres_url_sync

    if not config.get_main_option('sqlalchemy.url'):
        # ``ensure_target_database`` reads ``settings.postgres_db`` which
        # only makes sense for the default settings-driven path.
        ensure_target_database()

    connectable = engine_from_config(
        {'sqlalchemy.url': url},
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )

    print('Running the migrations online')
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=_already_in_the_database,
        )

        with context.begin_transaction():
            context.run_migrations()
    print('Done')


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
