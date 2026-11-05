"""A bad deploy can be rolled back, and rolled forward again.

F3 found that `downgrade base` could not be rolled forward — orphaned enum
types survived and the next `upgrade` collided with them. Each plane was
squashed to one revision before there were deployments to carry forward,
and has gained revisions since (`alembic/` for the shared plane,
`alembic_tenant/` for an organisation's own database); the cycle is base,
head, base, head, and the same schema each time, for both. A data
migration's round trip is tested with rows in place beside it, as
0027's is below and t0025's is in
`test_an_existing_default_group_is_brought_to_what_igni_ships.py`.

These tests run against their own throwaway database, so they never touch
the shared one and cannot depend on test ordering. They are skipped when a
database cannot be created rather than failing, since that is an
environment limitation rather than a defect.
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text

from alembic import command

from tests.conftest import TEST_DB_URL_SYNC

_SCRATCH = f'igni_roundtrip_{uuid.uuid4().hex[:8]}'


def _admin_url() -> str:
    """The same server, but the ``postgres`` database, so the scratch one
    can be created and dropped."""
    base, _, _ = TEST_DB_URL_SYNC.rpartition('/')
    return f'{base}/postgres'


def _scratch_url() -> str:
    base, _, _ = TEST_DB_URL_SYNC.rpartition('/')
    return f'{base}/{_SCRATCH}'


@pytest.fixture(scope='module')
def scratch_db():
    """A database of this module's own, created and dropped around it."""
    admin = create_engine(_admin_url(), isolation_level='AUTOCOMMIT')
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS {_SCRATCH}'))
            conn.execute(text(f'CREATE DATABASE {_SCRATCH}'))
    except Exception as exc:  # pragma: no cover - environment dependent
        admin.dispose()
        pytest.skip(f'cannot create a scratch database here: {type(exc).__name__}: {exc}')

    cfg = Config('alembic.ini')
    cfg.set_main_option('sqlalchemy.url', _scratch_url())
    try:
        yield cfg
    finally:
        admin_engine = create_engine(_admin_url(), isolation_level='AUTOCOMMIT')
        with admin_engine.connect() as conn:
            conn.execute(
                text(
                    'SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
                    f"WHERE datname = '{_SCRATCH}' AND pid <> pg_backend_pid()"
                )
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS {_SCRATCH}'))
        admin_engine.dispose()
        admin.dispose()


def _schema_fingerprint() -> list[str]:
    """Every table, index and constraint name, sorted.

    Names rather than full definitions: this is checking that a round trip
    puts the same objects back, and a name-level diff is what makes a
    failure readable.
    """
    engine = create_engine(_scratch_url())
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                    SELECT 'T:' || tablename FROM pg_tables WHERE schemaname = 'public'
                    UNION ALL
                    SELECT 'I:' || indexname FROM pg_indexes WHERE schemaname = 'public'
                    UNION ALL
                    SELECT 'C:' || c.conname FROM pg_constraint c
                      JOIN pg_class t ON t.oid = c.conrelid
                      JOIN pg_namespace n ON n.oid = t.relnamespace
                     WHERE n.nspname = 'public'
                    ORDER BY 1
                    """)).all()
        return sorted(r[0] for r in rows)
    finally:
        engine.dispose()


def _count(sql: str) -> int:
    engine = create_engine(_scratch_url())
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql)).scalar_one()
    finally:
        engine.dispose()


class TestTheFullCycle:
    """Ordered deliberately: each test leaves the database where the next
    one needs it, and the numbered names encode that. Split into steps
    rather than written as one long test so a failure names the step it
    failed at.

    That ordering is a real constraint: adding `pytest-randomly` or
    `pytest-random-order` to this project would break these. Neither is
    installed today (checked), and this note is here so the breakage is
    understood rather than mysterious if one ever is.
    """

    def test_1_upgrade_from_empty(self, scratch_db):
        command.upgrade(scratch_db, 'head')

        assert _count("SELECT count(*) FROM pg_tables WHERE schemaname='public'") > 8

    def test_2_downgrade_to_base_leaves_nothing_but_alembics_own_table(self, scratch_db):
        command.downgrade(scratch_db, 'base')

        remaining = _count("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
        assert remaining == 1, remaining
        # F3: orphaned enum types survived a downgrade and collided with the
        # next upgrade. Asserted directly because that is how it broke.
        assert (
            _count(
                'SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace '
                "WHERE n.nspname='public' AND t.typtype='e'"
            )
            == 0
        )

    def test_3_the_round_trip_restores_the_same_schema(self, scratch_db):
        command.upgrade(scratch_db, 'head')
        first = _schema_fingerprint()

        command.downgrade(scratch_db, 'base')
        command.upgrade(scratch_db, 'head')
        second = _schema_fingerprint()

        assert first == second, (
            'the schema differs after a base round trip:\n'
            f'  only before: {sorted(set(first) - set(second))}\n'
            f'  only after:  {sorted(set(second) - set(first))}'
        )


class TestRunningAlembicDoesNotSilenceTheApplication:
    """`fileConfig` disables every logger it does not name, unless told not to.

    This is the cause of a mystery this repository had worked around:
    `tests/test_webhook_failures_are_client_errors.py` clears
    `logger.disabled` by hand and records that the cause "is not
    identified". It was `alembic/env.py` calling `fileConfig` with the
    default `disable_existing_loggers=True` — and `conftest` builds the
    schema with `alembic upgrade head`, so it happened in every session that
    touched the database.

    Found by accident: this module runs alembic, and adding it broke two
    `test_ses_transport.py` tests that read `caplog.text`. They passed in
    isolation and failed when this module ran first.

    Scope worth stating precisely: **igni already passes
    `disable_existing_loggers=False`** in its own `migrations/env.py`, so
    this does not explain the same-shaped mystery documented over there.
    That one has a different, still-unidentified cause — its
    `test_http_retry.py` records that `dictConfig` was already ruled out.
    """

    def test_an_app_logger_survives_running_a_migration(self, scratch_db):
        import logging

        probe = logging.getLogger('app.services.email.transports.ses')
        probe.disabled = False

        command.upgrade(scratch_db, 'head')

        assert not probe.disabled, (
            'running a migration disabled an application logger. Every test '
            'asserting on log output after this point would see nothing, and '
            'a deployment that migrates in-process would go quiet.'
        )

    def test_env_py_opts_out_explicitly(self):
        """Asserted on the source because the default is the dangerous one:
        a future edit that drops the argument reintroduces this silently,
        and the symptom appears in an unrelated test file."""
        import pathlib

        env = (pathlib.Path(__file__).resolve().parent.parent / 'alembic/env.py').read_text()

        assert 'disable_existing_loggers=False' in env, (
            'alembic/env.py must pass disable_existing_loggers=False; the '
            'fileConfig default is True, which disables every app.* logger'
        )


class TestTheFleetOutlivesTheOrgThatAddedIt:
    """`inference_nodes.org_id` was `NOT NULL ON DELETE CASCADE`.

    The fleet is not the organisation's. `InferenceNodeDB.list_all` says so:
    "the inference fleet belongs to the deployment rather than to an
    organization within it, and `org_id` records who added the node, not who
    may use it." CASCADE gave provenance ownership of the lifecycle, so
    deleting an organisation deleted GPU nodes every other organisation was
    routing to — reachable from `DELETE /v1/portal/organizations/{org_id}`,
    and first noticed as chat failing for people unconnected to the org that
    was retired.

    Verified before the fix: insert a node owned by an org, delete the org,
    zero rows remain.

    Runs on the shared scratch database from the fixtures above.
    """

    def _seed(self) -> None:
        engine = create_engine(_scratch_url())
        now = datetime.now(timezone.utc)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        'INSERT INTO users (id, user_id, email, hashed_password, auth_provider, '
                        'created_at, updated_at) VALUES (:i, :u, :e, :h, :a, :n, :n)'
                    ),
                    {
                        'i': str(uuid.uuid4()),
                        'u': 'fleet-owner',
                        'e': 'fleet@example.com',
                        'h': '',
                        'a': 'email',
                        'n': now,
                    },
                )
                conn.execute(
                    text(
                        'INSERT INTO organizations (id, slug, display_name, owner_user_id, '
                        'created_at, updated_at) VALUES (:i, :s, :d, :o, :n, :n)'
                    ),
                    {'i': 'org-fleet', 's': 'fleet-org', 'd': 'Fleet', 'o': 'fleet-owner', 'n': now},
                )
                conn.execute(
                    text(
                        'INSERT INTO inference_nodes (id, node_id, org_id, base_url, models, '
                        'created_at, updated_at) VALUES (:i, :nid, :o, :u, :m, :n, :n)'
                    ),
                    {
                        'i': str(uuid.uuid4()),
                        'nid': 'gh200-shared-01',
                        'o': 'org-fleet',
                        'u': 'http://gpu-1:8000',
                        'm': '["igni-27b"]',
                        'n': now,
                    },
                )
        finally:
            engine.dispose()

    def test_6_deleting_the_registering_org_leaves_the_node_running(self, scratch_db):
        command.upgrade(scratch_db, 'head')
        self._seed()

        assert _count("SELECT count(*) FROM inference_nodes WHERE node_id = 'gh200-shared-01'") == 1

        engine = create_engine(_scratch_url())
        try:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM organizations WHERE id = 'org-fleet'"))
        finally:
            engine.dispose()

        assert _count("SELECT count(*) FROM inference_nodes WHERE node_id = 'gh200-shared-01'") == 1, (
            'deleting the organisation that registered a node removed the node — '
            'GPU capacity the whole deployment routes to, taken out by retiring one org'
        )
        assert (
            _count("SELECT count(*) FROM inference_nodes WHERE node_id = 'gh200-shared-01' AND org_id IS NULL") == 1
        ), 'the node survived but kept a dangling org_id'
        # The part that has to still work: routing reads base_url.
        engine = create_engine(_scratch_url())
        try:
            with engine.connect() as conn:
                url = conn.execute(
                    text("SELECT base_url FROM inference_nodes WHERE node_id = 'gh200-shared-01'")
                ).scalar_one()
        finally:
            engine.dispose()
        assert url == 'http://gpu-1:8000'


class TestAnOrganisationsDatabaseRoundTrips:
    """The same cycle for `alembic_tenant/`, which every organisation's own
    database is built from. A downgrade that left a trigger function behind
    would collide with the next upgrade, exactly as F3's enum types did."""

    @pytest.fixture
    def tenant_cfg(self, scratch_db):
        cfg = Config()
        cfg.set_main_option('script_location', 'alembic_tenant')
        cfg.set_main_option('sqlalchemy.url', _scratch_url())
        command.downgrade(scratch_db, 'base')
        yield cfg
        command.downgrade(cfg, 'base')
        command.upgrade(scratch_db, 'head')

    def test_it_upgrades_downgrades_and_upgrades_to_the_same_schema(self, tenant_cfg):
        command.upgrade(tenant_cfg, 'head')
        first = _schema_fingerprint()

        command.downgrade(tenant_cfg, 'base')
        assert _count("SELECT count(*) FROM pg_proc WHERE proname = 'audit_events_are_append_only'") == 0
        command.upgrade(tenant_cfg, 'head')

        assert _schema_fingerprint() == first
        assert _count("SELECT count(*) FROM pg_trigger WHERE tgname = 'audit_events_no_update'") == 1


class TestTheNodeKeyMigrationCarriesTheRowsThere:
    """0027 (F150): portal-added GPU nodes' API keys, stored as given until
    then, are encrypted in place — and decrypted back on a downgrade, so a
    rolled-back deploy still routes to them. Run on a row, not on an empty
    table, because the empty case is the one the cycle above already
    passes."""

    def _key(self) -> str:
        engine = create_engine(_scratch_url())
        try:
            with engine.connect() as conn:
                return conn.execute(text("SELECT api_key FROM inference_nodes WHERE node_id = 'gpu-1'")).scalar_one()
        finally:
            engine.dispose()

    def test_upgrade_encrypts_and_downgrade_restores(self, scratch_db, monkeypatch):
        from cryptography.fernet import Fernet

        from app.config import settings
        from app.services.secrets import decrypt_secret

        # Its own key, as every other test that encrypts has: CI exports
        # SECRET_ENCRYPTION_KEY for the run, a local `pytest` of this file
        # alone does not.
        monkeypatch.setattr(settings, 'secret_encryption_key', Fernet.generate_key().decode())

        command.upgrade(scratch_db, 'head')
        command.downgrade(scratch_db, '0026')
        engine = create_engine(_scratch_url())
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        'INSERT INTO inference_nodes (id, node_id, base_url, models, api_key) '
                        "VALUES ('n1', 'gpu-1', 'http://gpu.internal', '[]'::jsonb, 'plain-node-key')"
                    )
                )
        finally:
            engine.dispose()

        command.upgrade(scratch_db, 'head')
        stored = self._key()
        assert stored != 'plain-node-key'
        assert decrypt_secret(stored) == 'plain-node-key'

        command.downgrade(scratch_db, '0026')
        assert self._key() == 'plain-node-key'

        command.upgrade(scratch_db, 'head')
        assert decrypt_secret(self._key()) == 'plain-node-key'
