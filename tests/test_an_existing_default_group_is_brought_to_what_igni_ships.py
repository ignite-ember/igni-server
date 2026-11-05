"""An organisation that already had a Default group gets the seed a new one gets.

The Default group is seeded once and is read-only to admins, so a refresh
of the seed (df349c3f) reached new organisations only: the ones that
existed kept `plan_researcher` naming `Read`, `Grep`, `Glob` and `LS`,
and kept `resolve-issues`. `t0025` rewrites them.

Against a throwaway database of this module's own, built from
`alembic_tenant/` alone, so it never touches the suite's and cannot
depend on test ordering. Skipped when a database cannot be created.

The head assertion compares with the live `seed_entries()` on purpose:
a later `make refresh-default-seed` without a migration to carry it
fails here, which is the gap this migration closes.
"""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from types import ModuleType
import uuid

from alembic.config import Config
import pytest
from sqlalchemy import create_engine, Engine, text

from alembic import command

from app.services.portal.agent_tools import unknown_tools
from app.services.portal.default_group import seed_entries
from tests.conftest import TEST_DB_URL_SYNC

_SCRATCH = f'igni_seed_{uuid.uuid4().hex[:8]}'
_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / 'alembic_tenant'
    / 'versions'
    / 't0025_the_default_group_is_what_igni_ships_today.py'
)
_LONG_AGO = datetime(2026, 10, 1, tzinfo=timezone.utc)

Entries = dict[tuple[str, str], tuple[str, str, bool]]


def _url(database: str) -> str:
    base, _, _ = TEST_DB_URL_SYNC.rpartition('/')
    return f'{base}/{database}'


def _the_migration() -> ModuleType:
    """t0025 itself, for its frozen snapshots. Its name starts with a digit."""
    spec = importlib.util.spec_from_file_location('t0025_seed', _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


T0025 = _the_migration()


@pytest.fixture(scope='module')
def tenant():
    """A tenant database at t0024, created and dropped around the module."""
    admin = create_engine(_url('postgres'), isolation_level='AUTOCOMMIT')
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE {_SCRATCH}'))
    except Exception as exc:  # pragma: no cover - environment dependent
        admin.dispose()
        pytest.skip(f'cannot create a scratch database here: {type(exc).__name__}: {exc}')

    cfg = Config()
    cfg.set_main_option('script_location', 'alembic_tenant')
    cfg.set_main_option('sqlalchemy.url', _url(_SCRATCH))
    engine = create_engine(_url(_SCRATCH))
    try:
        yield cfg, engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(
                text(
                    'SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
                    f"WHERE datname = '{_SCRATCH}' AND pid <> pg_backend_pid()"
                )
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS {_SCRATCH}'))
        admin.dispose()


def _group(engine: Engine, group_id: str, *, name: str, is_default: bool) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                'INSERT INTO org_groups (id, org_id, name, is_default, created_by_user_id, created_at, updated_at) '
                "VALUES (:id, 'org-1', :name, :is_default, 'admin-1', :at, :at)"
            ),
            {'id': group_id, 'name': name, 'is_default': is_default, 'at': _LONG_AGO},
        )


def _entry(engine: Engine, group_id: str, kind: str, name: str, content: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                'INSERT INTO org_group_entries '
                '(id, group_id, kind, entry_name, content, content_type, enabled, created_at, updated_at) '
                'VALUES (:id, :group_id, :kind, :name, :content, :content_type, true, :at, :at)'
            ),
            {
                'id': str(uuid.uuid4()),
                'group_id': group_id,
                'kind': kind,
                'name': name,
                'content': content,
                'content_type': T0025._CONTENT_TYPES[kind],
                'at': _LONG_AGO,
            },
        )


def _entries(engine: Engine, group_id: str) -> Entries:
    with engine.connect() as conn:
        rows = conn.execute(
            text('SELECT kind, entry_name, content, content_type, enabled FROM org_group_entries WHERE group_id = :g'),
            {'g': group_id},
        ).all()
    return {(row.kind, row.entry_name): (row.content, row.content_type, row.enabled) for row in rows}


def _updated_at(engine: Engine, group_id: str) -> datetime:
    with engine.connect() as conn:
        return conn.execute(text('SELECT updated_at FROM org_groups WHERE id = :g'), {'g': group_id}).scalar_one()


def _as_entries(seed: list[tuple[str, str, str]]) -> Entries:
    return {(kind, name): (content, T0025._CONTENT_TYPES[kind], True) for kind, name, content in seed}


OTHER_GROUP: list[tuple[str, str, str]] = [
    # An admin's own copy, made from the old Default group and then edited:
    # stale names and all, it is theirs.
    ('agents', 'plan_researcher', '---\nname: plan_researcher\ntools: Read, Grep, Bash\n---\nMine.\n'),
    ('skills', 'resolve-issues', 'kept by an admin'),
    ('agents', 'contracts', '---\nname: contracts\n---\nReviews contracts.\n'),
]


@pytest.fixture(scope='module')
def migrated(tenant):
    """An organisation seeded before df349c3f, plus groups of an admin's, taken to head."""
    cfg, engine = tenant
    command.upgrade(cfg, 't0024')

    _group(engine, 'default', name='Default', is_default=True)
    for kind, name, content in T0025._previous_seed():
        _entry(engine, 'default', kind, name, content)
    # Seeded for a few hours in October, before the CodeIndex agents left.
    _entry(engine, 'default', 'agents', 'architect.codeindex', '---\nname: architect\n---\nOld.\n')
    # And one entry deleted while the group was still editable.
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM org_group_entries WHERE group_id = 'default' AND entry_name = 'qa'"))

    _group(engine, 'legal', name='Legal', is_default=False)
    for kind, name, content in OTHER_GROUP:
        _entry(engine, 'legal', kind, name, content)
    _group(engine, 'empty', name='Empty', is_default=False)

    command.upgrade(cfg, 'head')
    return engine


class TestTheUpgrade:
    def test_the_default_group_is_exactly_the_seed_a_new_organisation_gets(self, migrated):
        assert _entries(migrated, 'default') == _as_entries(seed_entries())

    def test_plan_researcher_no_longer_names_tools_igni_does_not_have(self, migrated):
        content, _, _ = _entries(migrated, 'default')[('agents', 'plan_researcher')]

        assert unknown_tools(content) == []

    def test_what_is_no_longer_seeded_is_gone(self, migrated):
        names = {name for _, name in _entries(migrated, 'default')}

        assert 'resolve-issues' not in names
        assert 'architect.codeindex' not in names

    def test_a_missing_entry_is_put_back(self, migrated):
        assert ('agents', 'qa') in _entries(migrated, 'default')

    def test_the_default_group_says_it_changed(self, migrated):
        assert _updated_at(migrated, 'default') > _LONG_AGO

    def test_every_other_group_is_left_as_it_was(self, migrated):
        assert _entries(migrated, 'legal') == _as_entries(OTHER_GROUP)
        assert _entries(migrated, 'empty') == {}
        assert _updated_at(migrated, 'legal') == _LONG_AGO
        assert _updated_at(migrated, 'empty') == _LONG_AGO


class TestTheFrozenSnapshots:
    def test_the_previous_seed_is_the_one_that_drifted(self):
        """So the test above starts from what an old organisation holds."""
        previous = {(kind, name): content for kind, name, content in T0025._previous_seed()}

        assert ('skills', 'resolve-issues') in previous
        assert unknown_tools(previous[('agents', 'plan_researcher')]) == ['Read', 'Grep', 'Glob', 'LS']

    def test_the_new_seed_warns_about_no_tool(self):
        assert [(name, unknown_tools(content)) for kind, name, content in T0025._SEED if unknown_tools(content)] == []


class TestTheWayBack:
    """Ordered: each leaves the database at head for the next."""

    def test_a_downgrade_restores_the_previous_seed_and_an_upgrade_the_new_one(self, tenant, migrated):
        cfg, engine = tenant

        command.downgrade(cfg, 't0024')
        assert _entries(engine, 'default') == _as_entries(T0025._previous_seed())
        assert _entries(engine, 'legal') == _as_entries(OTHER_GROUP)

        command.upgrade(cfg, 'head')
        assert _entries(engine, 'default') == _as_entries(seed_entries())

    def test_a_group_already_at_the_seed_is_not_touched(self, tenant, migrated):
        """The pack's ETag is a hash of the entries; an unchanged group keeps it."""
        cfg, engine = tenant
        with engine.begin() as conn:
            conn.execute(text("UPDATE org_groups SET updated_at = :at WHERE id = 'default'"), {'at': _LONG_AGO})
            conn.execute(
                text("UPDATE org_group_entries SET updated_at = :at WHERE group_id = 'default'"), {'at': _LONG_AGO}
            )

        command.downgrade(cfg, 't0024')
        command.upgrade(cfg, 'head')
        with engine.connect() as conn:
            untouched = (
                conn.execute(
                    text("SELECT entry_name FROM org_group_entries WHERE group_id = 'default' AND updated_at = :at"),
                    {'at': _LONG_AGO},
                )
                .scalars()
                .all()
            )

        # What the previous seed already had right comes through both ways untouched.
        previous = {(kind, name) for kind, name, _ in T0025._BEFORE}
        unchanged = sorted(name for kind, name, _ in seed_entries() if (kind, name) not in previous)
        assert sorted(untouched) == unchanged
