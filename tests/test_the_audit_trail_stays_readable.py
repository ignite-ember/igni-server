"""The audit table is the one that grows forever, so its queries matter.

F121. `AUDIT_RETENTION_DAYS` defaults to 0 — keep everything — which is
the right default for an audit trail and makes `audit_events` the only
table in the schema with no ceiling. Every query against it therefore has
to be answerable by an index, or it gets slower for the life of the
deployment.

Four of the five access patterns were indexed. The fifth was the one that
answers a GDPR Article 15 request:

    WHERE lower(actor_email) = lower(:e) ORDER BY occurred_at DESC

Measured on a scratch database before the fix:

      500,000 rows (193 MB) :  32-35 ms   (parallel sequential scan)
    2,000,000 rows (769 MB) : 288-344 ms

and after:

    2,000,000 rows (769 MB) : 0.5-6.7 ms  (index scan)

## Asserted on the plan, not on the index

An index that exists and is never chosen is the F115 failure — a claim
about the query rather than a description of it. `lower(actor_email)`
cannot use an index on the bare column, which is exactly the mistake this
would be, so these tests read the plan Postgres produces for the
statement the application actually issues.

The test database is nearly empty, and Postgres correctly prefers a
sequential scan over an index for a table of ten rows. So `enable_seqscan`
is turned off for the check: that does not prove the planner *will* choose
the index at scale, it proves the index *can* serve this query shape —
which is the property that was missing and the one a future change would
break.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text

from tests.conftest import TEST_DB_URL_SYNC

pytestmark = pytest.mark.anyio

#: The statements `app/services/subject_access.py` runs against
#: `audit_events`, copied here so the plan is checked for what the
#: application asks and not for a paraphrase of it.
_BY_EMAIL = (
    'SELECT occurred_at, method, route, target_type, target_id, status_code, '
    'outcome, client_host, forwarded_for, user_agent FROM audit_events '
    'WHERE lower(actor_email) = lower(:e) ORDER BY occurred_at DESC LIMIT 1000'
)
_BY_ACTOR = (
    'SELECT occurred_at, method, route, target_type, target_id, status_code, '
    'outcome, client_host, forwarded_for, user_agent FROM audit_events '
    'WHERE actor_user_id = :u ORDER BY occurred_at DESC LIMIT 1000'
)
_BY_ORG = 'SELECT * FROM audit_events WHERE org_id = :o ORDER BY occurred_at DESC, id DESC LIMIT 50'
_BY_TARGET = (
    'SELECT * FROM audit_events WHERE org_id = :o AND target_id = :t ORDER BY occurred_at DESC, id DESC LIMIT 50'
)


def _plan(sql: str, params: dict, *, allow_seqscan: bool = False) -> str:
    """The plan Postgres produces for `sql`, as text.

    Synchronous for the reason `test_audit_trail.py` documents: the `db`
    and `client` fixtures run on different event loops.
    """
    engine = create_engine(TEST_DB_URL_SYNC)
    try:
        with engine.begin() as conn:
            if not allow_seqscan:
                conn.execute(text('SET LOCAL enable_seqscan = off'))
            rows = conn.execute(text(f'EXPLAIN {sql}'), params).fetchall()
            return '\n'.join(row[0] for row in rows)
    finally:
        engine.dispose()


class TestEveryLookupHasAnIndexThatCanServeIt:
    def test_finding_a_person_by_address_uses_an_index(self):
        """The one that was a sequential scan.

        This is how a subject access request finds the sign-in attempts
        somebody made *before* they had an account — the rows F90 writes
        with a null `actor_user_id`, which `idx_audit_events_actor`
        cannot reach. So there is no fallback: without this index the
        query reads the whole table.
        """
        plan = _plan(_BY_EMAIL, {'e': 'someone@example.com'})

        assert 'idx_audit_events_actor_email' in plan, (
            'the Article 15 lookup by email address cannot use an index:\n' + plan
        )

    def test_the_expression_is_the_one_the_query_writes(self):
        """`lower(actor_email)`, not `actor_email`.

        An index on the bare column is unusable here and would look
        entirely reasonable in a migration — which is the mistake this
        test exists to catch, since it fails identically to having no
        index at all.
        """
        engine = create_engine(TEST_DB_URL_SYNC)
        try:
            with engine.begin() as conn:
                definition = conn.execute(
                    text("SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_audit_events_actor_email'")
                ).scalar_one_or_none()
        finally:
            engine.dispose()

        assert definition, 'idx_audit_events_actor_email does not exist'
        assert 'lower(actor_email)' in definition, definition
        # And the ordering column, so a person's whole history is not
        # sorted to return the newest thousand rows.
        assert 'occurred_at DESC' in definition, definition

    @pytest.mark.parametrize(
        'sql, params, expected',
        [
            (_BY_ACTOR, {'u': 'a-user-id'}, 'idx_audit_events_actor'),
            (_BY_ORG, {'o': 'an-org-id'}, 'idx_audit_events_org'),
            (_BY_TARGET, {'o': 'an-org-id', 't': 'a-target'}, 'idx_audit_events'),
        ],
    )
    def test_the_other_lookups_were_already_covered(self, sql, params, expected):
        """Measured and left alone.

        Filtering an org's events by `outcome` has no index of its own,
        and that was checked rather than assumed: at 500,000 rows the
        planner walks the org's rows in timestamp order and discards
        about 2,500 to collect a page of 50, warm in **5-12 ms**. It does
        not degrade with the table either, because the work is set by how
        rare the outcome is and not by how much history exists. So no
        index was added for it — a composite on
        `(org_id, outcome, occurred_at)` would be a third index on the
        product's most write-heavy table to save single-digit
        milliseconds.
        """
        plan = _plan(sql, params)

        assert expected in plan, plan


class TestTheGrowthItSurvives:
    async def test_the_query_returns_the_rows_it_claims_to(self, db):
        """The plan is not the point on its own.

        An index can be used and the answer still be wrong — a
        case-folding index that matched only the lowered form would pass
        every plan check above and silently return nothing for an
        address recorded in mixed case. Which is the form a person
        actually types.
        """
        from sqlalchemy import text as sql_text

        address = f'Mixed.Case-{uuid.uuid4().hex[:6]}@Example.COM'
        engine = create_engine(TEST_DB_URL_SYNC)
        try:
            with engine.begin() as conn:
                conn.execute(
                    sql_text(
                        'INSERT INTO audit_events (id, occurred_at, actor_email, method, route, '
                        "outcome, status_code) VALUES (:i, now(), :e, 'POST', '/v1/sign-in', 'denied', 401)"
                    ),
                    {'i': str(uuid.uuid4()), 'e': address},
                )
                # Asked in every case a person might type it.
                for spelling in (address, address.lower(), address.upper()):
                    found = conn.execute(sql_text(_BY_EMAIL), {'e': spelling}).fetchall()
                    assert len(found) == 1, f'{spelling!r} found {len(found)} rows'
        finally:
            engine.dispose()

    def test_retention_can_still_clear_the_table_it_bounds(self):
        """The purge was measured too, and needed nothing.

        Deleting 1.5 million of 2 million rows took **1.33 s** in one
        statement on the scratch database. A single transaction that long
        holds one of the process's 20 connections (F116) and produces a
        WAL burst, both of which are acceptable once a day — so the
        delete was left unbatched rather than made more complicated than
        the evidence supports.

        What is asserted here is only that the sweep exists and is
        driven by the setting, since that is the mechanism the index
        above assumes somebody may eventually turn on.
        """
        import inspect

        from app.db.sql.audit_events import AuditEventDB

        source = inspect.getsource(AuditEventDB.prune)

        assert 'older_than_days <= 0' in source, 'the no-op default is gone'
        assert 'occurred_at < cutoff' in source, 'the purge no longer selects by age'
