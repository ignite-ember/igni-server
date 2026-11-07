"""A subject access request can be answered. GDPR Article 15.

DP-8 in `COMPLIANCE_TRACKER.md`. Before this the only way to answer one
was SQL across twenty tables and a hundred and five personal columns,
which is not a plan — it is a promise to miss something.

Three properties are tested, and the last is the one that keeps this true
as the schema grows:

* the report contains what the deployment holds, **with** the Article 15
  context — purposes, categories, recipients, retention, source. Rows
  alone answer half the article;
* it never returns a credential, and never returns a **third party's**
  data. The second is subtle: an administrator's audit records describe
  the people they administered, so returning them whole would breach
  somebody else's rights in the act of honouring the administrator's;
* every column of every table that holds personal data is classified —
  as included, withheld or dead in `PERSONAL_DATA_COLUMNS`, or as not
  personal in `NOT_PERSONAL_COLUMNS` with the reason it is not — and
  every `included` column is returned by every query that reads its
  table. A new table with a `user_id` fails, and so does a new column on
  an existing one. F115.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import create_engine, text

from app.db.sql.tenant_migrator import TenantMigrator
from app.db.sql.tenant_names import TenantNames
from app.services.subject_access import (
    _ACTIVITY_LIMIT,
    _note_if_capped,
    describe_processing,
    NOT_PERSONAL_COLUMNS,
    PERSONAL_DATA_COLUMNS,
    SubjectAccessService,
)
from tests.conftest import (
    add_member,
    audit_rows,
    auth_header,
    create_org,
    create_user,
    make_token,
    TEST_DB_URL_SYNC,
)

pytestmark = pytest.mark.anyio


def _execute(sql: str, params: dict | None = None):
    """Synchronous, for the reason `test_audit_trail.py` documents: the
    `db` fixture and the `client` fixture run on different event loops."""
    engine = create_engine(TEST_DB_URL_SYNC)
    try:
        with engine.begin() as conn:
            result = conn.execute(text(sql), params or {})
            return result.fetchall() if result.returns_rows else []
    finally:
        engine.dispose()


def _live(sql: str, params: dict | None = None) -> set[str]:
    """`col` from `sql`, run against the shared plane and an organisation's schema.

    Half the personal data is in each organisation's own database now —
    groups, usage, notifications, the organisation's audit trail. Every
    one has the same schema, the template's, so that is where it is read.
    """
    TenantMigrator().ensure_template()
    found = {r.col for r in _execute(sql, params)}
    base, _, _ = TEST_DB_URL_SYNC.rpartition('/')
    engine = create_engine(f'{base}/{TenantNames().template()}')
    try:
        with engine.connect() as conn:
            found |= {r.col for r in conn.execute(text(sql), params or {}).fetchall()}
    finally:
        engine.dispose()
    return found


def _export_statements() -> list[tuple[str, set[str], set[str]]]:
    """Every SELECT the export runs: its table, its columns, its match keys.

    Parsed from the module's own SQL rather than from a second list that
    can drift. The strings are built by implicit concatenation, which
    Python folds at parse time — so `ast` hands back each complete
    statement and there is no line-wrapping to defeat this, which is how
    six earlier attempts at source-reading rules failed.

    Kept per statement rather than merged per table. A merged view is
    what let a revert slip through while this rule was being written: the
    export reads `audit_events` twice — once keyed on the user id for
    somebody with an account, once on the address for somebody without —
    and a column dropped from one query was covered by the other. The
    person with no account is precisely the one who would notice.
    """
    import ast
    import pathlib
    import re

    # The statements moved to the database layer; the classification
    # they are checked against stayed in the service. This reads the
    # queries.
    source = (pathlib.Path(__file__).resolve().parent.parent / 'app/db/sql/subject_access.py').read_text()
    statements: list[tuple[str, set[str], set[str]]] = []

    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if 'SELECT ' not in node.value or ' FROM ' not in node.value:
            continue
        sql = ' '.join(node.value.split())
        parts = re.match(r'SELECT (.+?) FROM (\w+)\b(.*)$', sql)
        assert parts, sql
        selected = {column.strip().split()[0] for column in parts.group(1).split(',')}
        # Column names on the left of a comparison, through a `lower()`
        # wrapper or a `::text` cast.
        matched = set(re.findall(r'(?:^|[\s(])([a-z_]+)\)?(?:::[a-z_]+)?\s*(?:=|ILIKE)', parts.group(3)))
        statements.append((parts.group(2), selected, matched))

    return statements


class TestTheCoverageRule:
    """The check that outlives everything else in this file.

    Three rules, and they cover different failures — which is the whole
    lesson of F113 and F115. The first *discovers* tables: a new one with
    a `user_id` fails until somebody classifies it. The second gives that
    discovery **depth**: once a table is known to hold personal data,
    every one of its columns has to be judged, because `waitlist` was
    listed for its `email` and its other nine columns were invisible. The
    third makes `included` mean something: the export must actually
    return or match on the column, or the map is a claim about the export
    rather than a description of it.
    """

    def test_every_personal_column_is_accounted_for(self):
        """Discovery. Read from the live database, not from a list.

        A migration that adds a table with a `user_id` should fail here
        until somebody decides whether it belongs in an export.
        """
        live = _live("""
            SELECT c.table_name || '.' || c.column_name AS col
            FROM information_schema.columns c
            JOIN information_schema.tables t
              ON t.table_name = c.table_name AND t.table_schema = 'public'
            WHERE c.table_schema = 'public'
              AND (c.column_name LIKE '%user_id%' OR c.column_name LIKE '%email%'
                   OR c.column_name LIKE '%login%' OR c.column_name LIKE '%created_by%'
                   OR c.column_name LIKE '%_by%' OR c.column_name LIKE '%actor%')
            """)

        assert len(live) > 20, f'only found {len(live)} person-referencing columns'

        known = set(PERSONAL_DATA_COLUMNS) | NOT_PERSONAL_COLUMNS
        unaccounted = sorted(live - known)
        assert not unaccounted, (
            'these columns reference a person and the subject access export does not '
            f'account for them: {unaccounted}. Add each to PERSONAL_DATA_COLUMNS as '
            "'included', 'withheld' or 'dead' — an Article 15 answer that quietly "
            'omits a table is a wrong answer, not an incomplete one.'
        )

    def test_every_column_of_every_personal_table_is_classified(self):
        """Depth. The rule F113 asked for and could not have been passed by
        a pattern match.

        `waitlist` was in the map for its `email` alone, and the export
        returned `email, created_at` — so an Article 15 answer omitted the
        subject's name, employer, job title and the free text they wrote.
        The pattern rule above could not see it: it matches names that
        **identify** a person, and those columns **describe** one.

        So: once a table appears in either collection, every one of its
        columns must appear too, each one judged in writing. A new column on an
        existing table fails here — which is the case the pattern rule
        misses entirely, because nothing about `full_name` looks like an
        identifier.
        """
        known = set(PERSONAL_DATA_COLUMNS) | NOT_PERSONAL_COLUMNS
        tables = {col.split('.')[0] for col in known}
        assert len(tables) >= 12, f'only {len(tables)} tables classified — has the map shrunk?'

        live = _live(
            "SELECT table_name || '.' || column_name AS col FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = ANY(:t)",
            {'t': sorted(tables)},
        )
        assert len(live) > 110, f'only {len(live)} columns found across {len(tables)} tables'

        unjudged = sorted(live - known)
        assert not unjudged, (
            f'these columns are in tables known to hold personal data and nobody has '
            f'said whether they are personal: {unjudged}. Put each in '
            'PERSONAL_DATA_COLUMNS (included / withheld / dead) or in '
            'NOT_PERSONAL_COLUMNS with the group whose reason fits. A table known to '
            'hold personal data holds it in all its columns until somebody says '
            'otherwise — that is F113, and it cost a name, an employer and a job title.'
        )

    def test_nothing_is_both_personal_and_not(self):
        """The two collections partition the columns. An entry in both
        means the rule above passes while the question is unanswered."""
        both = sorted(set(PERSONAL_DATA_COLUMNS) & NOT_PERSONAL_COLUMNS)
        assert not both, f'classified as both personal and not personal: {both}'

    def test_every_included_column_is_returned_by_every_query_that_could(self):
        """`included` has to mean the export produces it — in every query.

        Match keys are exempt from being selected: `chat_usage.user_id` is
        the subject's own id and the section is already theirs, so keying
        on it is disclosure enough and selecting it into every row would
        be noise. A column that is a match key for *any* query against a
        table is treated as an identity column for all of them.

        Everything else marked `included` must appear in the select list
        of **each** statement reading that table. Per statement, because
        the union across statements is what hid a deliberately broken
        revert while this rule was being written: drop `forwarded_for`
        from the query used for somebody with no account, and the query
        used for somebody with one still covers the column. The subject
        with no account is exactly the one who loses the data.
        """
        statements = _export_statements()
        assert len(statements) >= 12, (
            f'only parsed {len(statements)} statements out of the export — has its SQL changed shape?'
        )

        identity: dict[str, set[str]] = {}
        for table, _selected, matched in statements:
            identity.setdefault(table, set()).update(matched)

        required: dict[str, set[str]] = {}
        for column, verdict in PERSONAL_DATA_COLUMNS.items():
            if verdict != 'included':
                continue
            table, name = column.split('.')
            required.setdefault(table, set()).add(name)

        absent: list[str] = []
        for table, selected, _matched in statements:
            missing = required.get(table, set()) - selected - identity.get(table, set())
            absent += [f'{table}.{name} (from SELECT {", ".join(sorted(selected))})' for name in sorted(missing)]

        assert not absent, (
            f'these columns are marked `included` and a query that reads their table '
            f'neither returns nor matches on them: {absent}. Add them to that SELECT, or '
            'change the verdict to `withheld` and say in the report why the value is '
            'missing — silently omitting one makes the export look complete when it is not.'
        )

        # The rule has to have looked at the tables that matter, or an
        # empty parse would pass it in silence.
        assert len(required) >= 11, f'only {len(required)} tables have `included` columns — has the map shrunk?'

    def test_every_withheld_column_is_explained_in_the_report(self):
        """Withholding is only honest if the report says so.

        The alternative is an export that looks complete and is not, which
        is the failure this whole file exists to prevent. Asserted against
        a real report rather than against the source.
        """
        import asyncio

        report = asyncio.run(SubjectAccessService().export(email='nobody-here@example.com', org_id=None))

        withheld = {col for col, verdict in PERSONAL_DATA_COLUMNS.items() if verdict == 'withheld'}
        assert withheld, 'nothing is withheld — has the map changed shape?'

        unexplained = sorted(withheld - set(report.withheld))
        assert not unexplained, (
            f'these columns are withheld and the report does not say so: {unexplained}. '
            'The subject is told what exists and why they are not getting it; that is '
            'the difference between a withheld field and a missing one.'
        )

    def test_the_list_has_not_gone_stale(self):
        live = _live("""
            SELECT c.table_name || '.' || c.column_name AS col
            FROM information_schema.columns c
            WHERE c.table_schema = 'public'
            """)

        gone = sorted((set(PERSONAL_DATA_COLUMNS) | NOT_PERSONAL_COLUMNS) - live)
        assert not gone, (
            f'the classification names columns that no longer exist: {gone}. A dropped '
            'table leaves entries behind that read as coverage of something real.'
        )

    def test_every_included_table_is_actually_queried(self):
        """`included` must mean the export reads it, not that somebody
        intended it to.

        `github_install_intents` was marked included and queried nowhere —
        found by comparing the list against the source rather than by
        reading either. Without this check the coverage list is a claim
        about the export rather than a description of it, which is worse
        than no list: it reads as assurance.
        """
        import pathlib
        import re

        # The statements moved to the database layer; the classification
        # they are checked against stayed in the service. This reads the
        # queries.
        source = (pathlib.Path(__file__).resolve().parent.parent / 'app/db/sql/subject_access.py').read_text()
        queried = set(re.findall(r'FROM (\w+)', source))
        claimed = {col.split('.')[0] for col, verdict in PERSONAL_DATA_COLUMNS.items() if verdict == 'included'}

        assert queried, 'no SQL found in the service — has it changed shape?'
        missing = sorted(claimed - queried)
        assert not missing, (
            f'these tables are marked `included` in PERSONAL_DATA_COLUMNS and the export '
            f'never reads them: {missing}. Either query them or change the verdict.'
        )

    def test_credentials_are_marked_withheld_not_included(self):
        """The two that must never be exported, asserted by name so a
        careless edit to the list is caught rather than shipped."""
        assert PERSONAL_DATA_COLUMNS['users.hashed_password'] == 'withheld'
        assert PERSONAL_DATA_COLUMNS['user_roles.email_verification_token'] == 'withheld'


class TestTheArticle15Context:
    """Rows alone answer half the article."""

    def test_it_states_purposes_categories_recipients_retention_and_source(self):
        processing = describe_processing()

        for required in ('purposes', 'categories', 'recipients', 'retention', 'source'):
            assert processing.get(required), f'the report has no {required}'
        assert 'self-hosted' in processing['controller']

    def test_recipients_reflect_what_is_configured(self, monkeypatch):
        """Generated, not boilerplate. A deployment with no relay and no
        bucket sends nothing anywhere, and the report should say so rather
        than list recipients it does not have."""
        from app.config import settings

        monkeypatch.setattr(settings, 'email_enabled', False)
        monkeypatch.setattr(settings, 'smtp_host', '')

        recipients = describe_processing()['recipients']

        assert len(recipients) == 1
        assert 'none' in recipients[0].lower()

    def test_a_configured_relay_appears_as_a_recipient(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, 'email_enabled', True)
        monkeypatch.setattr(settings, 'smtp_host', 'mail.corp.internal')

        recipients = describe_processing()['recipients']

        assert any('mail.corp.internal' in r for r in recipients), recipients

    def test_the_ses_api_backend_is_a_recipient_with_no_smtp_host(self, monkeypatch):
        """EMAIL_BACKEND=ses sends with no SMTP_HOST. The report decided
        "is email configured" by the host, so an SES deployment's export
        said its data went nowhere while every code went to Amazon."""
        from app.config import settings

        monkeypatch.setattr(settings, 'email_enabled', True)
        monkeypatch.setattr(settings, 'email_backend', 'ses')
        monkeypatch.setattr(settings, 'smtp_host', '')
        monkeypatch.setattr(settings, 'ses_region', 'eu-west-1')

        recipients = describe_processing()['recipients']

        assert any('Amazon SES in eu-west-1' in r for r in recipients), recipients
        assert not any('none' in r.lower() for r in recipients), recipients

    def test_retention_reports_the_window_actually_in_force(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, 'audit_retention_days', 0)
        assert 'indefinitely' in describe_processing()['retention']['audit_events']

        monkeypatch.setattr(settings, 'audit_retention_days', 365)
        assert '365 days' == describe_processing()['retention']['audit_events']


class TestTheExport:
    @pytest.fixture
    async def person(self, db):
        email = f'subject-{uuid.uuid4().hex[:8]}@example.com'
        user = await create_user(db, email=email)
        org = await create_org(db, owner_user_id=str(user.user_id), slug=f's-{uuid.uuid4().hex[:6]}')
        await add_member(db, str(org.id), str(user.user_id), role='member')
        return {'email': email, 'user_id': str(user.user_id), 'org_id': str(org.id)}

    async def test_it_finds_the_account_and_the_membership(self, person):
        report = await SubjectAccessService().export(email=person['email'])

        assert report.subject == person['email']
        assert report.sections['account'], report.sections
        assert report.sections['account'][0]['email'] == person['email']
        assert report.sections['organisation_memberships'], report.sections

    async def test_the_password_hash_is_never_in_the_report(self, person):
        _execute(
            'UPDATE users SET hashed_password = :h WHERE user_id = :u',
            {'h': '$2b$12$a-real-looking-hash-value', 'u': person['user_id']},
        )

        report = await SubjectAccessService().export(email=person['email'])

        assert 'a-real-looking-hash-value' not in json.dumps(report, default=str)
        # And its absence is declared rather than silent.
        assert 'users.hashed_password' in report.withheld

    async def test_an_address_with_no_account_still_gets_an_answer(self):
        """Somebody who never had an account can still make a request,
        and may appear in sign-in attempts. "We hold nothing" would be a
        false answer."""
        report = await SubjectAccessService().export(email='never-here@example.com')

        assert report.sections['account'] == []
        assert 'No user account exists' in report.sections['note']
        assert report.processing, 'the Article 15 context is missing'

    async def test_it_refuses_something_that_is_not_an_address(self):
        for bad in ('', 'jane', 'not-an-email'):
            with pytest.raises(ValueError):
                await SubjectAccessService().export(email=bad)

    async def test_an_audit_detail_naming_somebody_else_is_not_returned(self, person):
        """`audit_events.detail` is per-route JSON, and on some routes it is
        about a *third party*: the row F90 writes for an erasure carries
        the masked address of the person erased, and an access-review row
        carries the member reviewed. So an administrator's own audit
        history discloses the people they administered — returning it
        whole would breach their rights in the act of honouring the
        admin's request.

        It is withheld rather than filtered because the shape differs per
        route: there is no single rule for what to strip, and a wrong
        guess discloses. F115.
        """
        _execute(
            'INSERT INTO audit_events (id, occurred_at, actor_user_id, actor_email, method, route, '
            'outcome, status_code, detail) VALUES (:i, now(), :u, :e, %s, %s, %s, 200, CAST(:d AS jsonb))'
            % ("'POST'", "'/v1/portal/erasure'", "'allowed'"),
            {
                'i': str(uuid.uuid4()),
                'u': person['user_id'],
                'e': person['email'],
                'd': json.dumps({'erasure': {'subject': 'v***@example.com', 'events': 3}}),
            },
        )

        report = await SubjectAccessService().export(email=person['email'], org_id=person['org_id'])

        # The row itself is theirs and is returned.
        routes = [row['route'] for row in report.sections['own_activity']]
        assert '/v1/portal/erasure' in routes, report.sections['own_activity']

        # What it says about the other person is not.
        blob = json.dumps(report, default=str)
        assert 'v***@example.com' not in blob
        assert 'detail' not in report.sections['own_activity'][0]
        # And its absence is declared, not silent.
        assert 'audit_events.detail' in report.withheld

    async def test_their_own_address_is_returned_from_both_columns(self):
        """A deployment behind a load balancer records the peer in
        `client_host` and the person's real address in `forwarded_for`.
        Returning one and not the other omits whichever one is actually
        theirs, and which that is depends on the deployment — so both are
        returned. F115.
        """
        email = f'proxied-{uuid.uuid4().hex[:8]}@example.com'
        _execute(
            'INSERT INTO audit_events (id, occurred_at, actor_email, method, route, outcome, '
            "status_code, client_host, forwarded_for, user_agent) VALUES (:i, now(), :e, 'POST', "
            "'/v1/auth/login', 'denied', 401, '10.0.0.7', :f, :a)",
            {
                'i': str(uuid.uuid4()),
                'e': email,
                'f': '203.0.113.9',
                'a': 'Mozilla/5.0 (a browser they used)',
            },
        )

        report = await SubjectAccessService().export(email=email)
        attempts = report.sections['authentication_attempts']

        assert attempts, 'the attempt was not returned at all'
        assert attempts[0]['client_host'] == '10.0.0.7'
        assert attempts[0]['forwarded_for'] == '203.0.113.9'
        assert attempts[0]['user_agent'] == 'Mozilla/5.0 (a browser they used)'


class TestTheEndpoint:
    @pytest.fixture
    async def admin(self, db):
        user = await create_user(db, email=f'admin-{uuid.uuid4().hex[:6]}@example.com')
        org = await create_org(db, owner_user_id=str(user.user_id), slug=f'a-{uuid.uuid4().hex[:6]}')
        await add_member(db, str(org.id), str(user.user_id), role='admin')
        return {'token': make_token(str(user.user_id), org_id=str(org.id), org_name='A')}

    async def test_an_admin_can_export(self, client, admin):
        response = await client.post(
            '/portal/subject-access',
            headers=auth_header(admin['token']),
            json={'email': 'anybody@example.com'},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body['subject'] == 'anybody@example.com'
        assert body['processing']['purposes']

    async def test_a_plain_member_cannot(self, client, db):
        member = await create_user(db, email=f'p-{uuid.uuid4().hex[:6]}@example.com')
        owner = await create_user(db, email=f'o-{uuid.uuid4().hex[:6]}@example.com')
        org = await create_org(db, owner_user_id=str(owner.user_id), slug=f'b-{uuid.uuid4().hex[:6]}')
        await add_member(db, str(org.id), str(member.user_id), role='member')
        token = make_token(str(member.user_id), org_id=str(org.id), org_name='B')

        response = await client.post(
            '/portal/subject-access',
            headers=auth_header(token),
            json={'email': 'anybody@example.com'},
        )

        assert response.status_code == 403, response.text

    async def test_it_is_audited_with_the_address_masked(self, client, admin):
        """F90 records this automatically because it is a `POST` under
        `/v1/portal`. The detail must carry counts, not the subject: the
        audit trail is retained, so an export of somebody's data must not
        leave their address sitting in it."""
        await client.post(
            '/portal/subject-access',
            headers=auth_header(admin['token']),
            json={'email': 'audited@example.com'},
        )

        rows = audit_rows(route='/v1/portal/subject-access')
        assert len(rows) == 1, rows
        assert 'audited@example.com' not in str(rows[0]['detail'] or '')
        assert 'a***@example.com' in str(rows[0]['detail'] or '')

    def test_the_address_is_not_in_a_query_string(self):
        """A `GET ?email=` would put the subject's address in the reverse
        proxy's access log, in browser history and in a referrer header —
        a disclosure created by the act of answering a disclosure request.
        Asserted on the source because the shape is the control."""
        import pathlib

        source = (
            pathlib.Path(__file__).resolve().parent.parent / 'app/api/base_v1/endpoints/portal/subject_access.py'
        ).read_text()

        assert '@router.post(' in source
        assert '@router.get(' not in source
        assert 'Query(' not in source, 'the address must arrive in the body, not the URL'


class TestATruncatedReportSaysSo:
    """`no silent caps`. This export is capped per section, and a cap that
    is not reported turns an incomplete legal answer into one that reads
    as complete — worse than refusing."""

    def test_a_full_section_is_declared_truncated(self):
        report: dict = {}
        _note_if_capped(report, 'own_activity', list(range(_ACTIVITY_LIMIT)), _ACTIVITY_LIMIT)

        # A plain dict here, not the model: `_note_if_capped` is the
        # internal builder helper and runs while the report is still
        # being assembled.
        assert 'own_activity' in report['truncated']
        assert 'There are more' in report['truncated']['own_activity']

    def test_a_short_section_is_not(self):
        """Otherwise every export claims truncation and the flag means
        nothing."""
        report: dict = {}
        _note_if_capped(report, 'own_activity', [1, 2, 3], _ACTIVITY_LIMIT)

        assert 'truncated' not in report

    def test_every_capped_query_reports_its_cap(self):
        """The rule, not the four call sites. A fifth section added later
        with a `LIMIT` and no disclosure fails here."""
        import pathlib
        import re

        # The statements moved to the database layer; the classification
        # they are checked against stayed in the service. This reads the
        # queries.
        source = (pathlib.Path(__file__).resolve().parent.parent / 'app/db/sql/subject_access.py').read_text()

        # `LIMIT 1000` or `LIMIT :lim` — the cap is a bound parameter
        # now, and a query that carries one still owes a disclosure.
        capped = len(re.findall(r'LIMIT (?:\d+|:\w+)', source))
        service = (pathlib.Path(__file__).resolve().parent.parent / 'app/services/subject_access.py').read_text()
        disclosed = len(re.findall(r'_note_if_capped\(', service)) - 1  # minus the definition

        assert capped == disclosed, (
            f'{capped} queries carry a LIMIT but only {disclosed} sections report being '
            'truncated. A section that silently stops at its cap tells the subject they '
            'have all their data when they do not.'
        )


class TestTheEnvelopeCarriesEverythingTheExportSets:
    """`SubjectAccessReport` is a model, and a model drops what it does
    not declare.

    On an Article 15 answer that is not a typing detail. The first
    version of this model listed `subject`, `generated_at`,
    `processing`, `withheld` and `sections`, and would have silently
    dropped `truncated` — the key whose entire job is to say the
    answer is incomplete. Reporting a capped section as complete is
    the failure the cap mechanism exists to prevent, and the model
    would have reintroduced it on the way out.

    So this asserts the model carries every top-level key the builder
    can set, discovered from the builder rather than written down
    twice.
    """

    def test_every_key_the_builder_sets_survives_validation(self):
        import ast
        import pathlib

        from app.services.subject_access import SubjectAccessReport

        source = pathlib.Path(__file__).resolve().parents[1] / 'app/services/subject_access.py'
        tree = ast.parse(source.read_text())

        # `report['x'] = ...`, `report.setdefault('x', ...)`, and the
        # literal the report starts as.
        assigned: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == 'report':
                if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                    assigned.add(node.slice.value)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'setdefault'
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == 'report'
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                assigned.add(node.args[0].value)
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == 'report':
                if isinstance(node.value, ast.Dict):
                    assigned |= {k.value for k in node.value.keys if isinstance(k, ast.Constant)}

        assert assigned, 'found no report keys — this check would be vacuous'
        declared = set(SubjectAccessReport.model_fields)
        missing = sorted(assigned - declared - {'sections'})
        assert not missing, f'{missing} are set on the report and would be dropped by the response model'
