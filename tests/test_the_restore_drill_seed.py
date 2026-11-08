"""The restore drill's seed fills what the drill dumps, the way the application makes it.

`tools/restore-drill.sh` runs `tools/restore_drill_seed.py` inside the
shipped image, then reads back the registry rows and counts it expects:
the first organisation active, the second detached with its database
kept, and different counts in each so an archive holding the other's
rows would not compare equal. The drill runs by hand and in CI's drill
job; this runs the seed against the test database so a change to it
fails here first.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.db.sql.models.tenant import ACTIVE, DETACHED
from app.db.sql.tenant_names import TenantNames
from tests.conftest import TEST_DB_URL_SYNC
from tools.restore_drill_seed import ACTIVE_ORG, DETACHED_ORG, DrillSeed, NODE_API_KEY

pytestmark = pytest.mark.anyio


def _rows(database: str, sql: str) -> list:
    base, _, _ = TEST_DB_URL_SYNC.rpartition('/')
    engine = create_engine(f'{base}/{database}', poolclass=NullPool)
    try:
        with engine.connect() as conn:
            return list(conn.execute(text(sql)).fetchall())
    finally:
        engine.dispose()


_SHARED_COUNTS = (
    'SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM organizations), '
    '(SELECT count(*) FROM org_memberships), (SELECT count(*) FROM org_domains), '
    '(SELECT count(*) FROM inference_nodes), (SELECT count(*) FROM tenants), (SELECT count(*) FROM audit_events)'
)
_ORGANISATION_COUNTS = (
    'SELECT (SELECT count(*) FROM org_groups), (SELECT count(*) FROM org_group_members), '
    '(SELECT count(*) FROM notifications), (SELECT count(*) FROM chat_usage), (SELECT count(*) FROM audit_events)'
)


async def test_it_provisions_both_organisations_and_detaches_the_second(capsys):
    await DrillSeed().run()

    names = TenantNames()
    assert [
        tuple(r) for r in _rows('ember_test', 'SELECT org_id, number, database, state FROM tenants ORDER BY number')
    ] == [
        (ACTIVE_ORG, 1, names.name(1), ACTIVE),
        (DETACHED_ORG, 2, names.name(2), DETACHED),
    ]
    assert capsys.readouterr().out.splitlines() == [
        'seeded: shared plane (1 user, 1 org, 1 membership, 1 domain, 1 inference node, 2 audit events); '
        'ember_test_t1 (active: 1 group, 1 member, 1 notification, 1 usage, 3 audit events); '
        'ember_test_t2 (detached: 1 group, 1 audit event)'
    ]


async def test_each_database_holds_its_own_counts_and_the_detached_one_keeps_its_data():
    await DrillSeed().run()

    assert tuple(_rows('ember_test', _SHARED_COUNTS)[0]) == (1, 1, 1, 1, 1, 2, 2)
    assert tuple(_rows('ember_test_t1', _ORGANISATION_COUNTS)[0]) == (1, 1, 1, 1, 3)
    assert tuple(_rows('ember_test_t2', _ORGANISATION_COUNTS)[0]) == (1, 0, 0, 0, 1)


async def test_the_node_key_and_its_non_ascii_models_are_stored_byte_for_byte_as_the_drill_compares_them():
    await DrillSeed().run()

    [(api_key, models)] = _rows('ember_test', 'SELECT api_key, models FROM inference_nodes')
    assert api_key == NODE_API_KEY
    assert models == ['drill-base', 'drill-adapter-zürich']
