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

from datetime import datetime, timezone
import inspect
import json
import re
import uuid

from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine, text

from app.config import settings
from app.db.sql.models.audit_event import AuditEvent
from app.db.sql.models.org_group import OrgGroup
from app.db.sql.models.org_group_member import OrgGroupMember
from app.db.sql.schemas.subject_access import AccountRow, AuditEventRow, GroupMembershipRow, ModelUsageRow
from app.db.sql.schemas.subject_access_created import ThingsTheyCreated
from app.db.sql.subject_access import ACTIVITY_LIMIT, SubjectAccessDB
from app.db.sql.subject_access_created import CreatedThingsDB
from app.db.sql.tenant_migrator import TenantMigrator
from app.db.sql.tenant_names import TenantNames
from app.services.identity.service_subjects import ExportAnswers
from app.services.subject_access.classification import ColumnRegistry
from app.services.subject_access.processing import ProcessingDescription
from app.services.subject_access.report_builder import SubjectAccessReportBuilder
from app.services.subject_access.schemas import (
    ColumnClassification,
    ColumnVerdict,
    InvalidSubjectAddress,
    Section,
    SubjectAccessReport,
    SubjectAccessSections,
)
from app.services.subject_access.service import SubjectAccessService
from tests.conftest import (
    add_member,
    audit_rows,
    auth_header,
    create_org,
    create_user,
    make_token,
    put_in_org,
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


def _export_sources() -> list[str]:
    """The source of every module that holds export SQL."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / 'app/db/sql'
    return [(root / name).read_text() for name in ('subject_access.py', 'subject_access_created.py')]


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
    import re

    # The statements moved to the database layer; the classification
    # they are checked against stayed in the service. This reads the
    # queries.
    statements: list[tuple[str, set[str], set[str]]] = []

    for node in (found for source in _export_sources() for found in ast.walk(ast.parse(source))):
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

        known = ColumnRegistry().known_columns()
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
        known = ColumnRegistry().known_columns()
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
        registry = ColumnRegistry()
        both = sorted(set(registry.personal()) & registry.not_personal())
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
        for column in ColumnRegistry().columns_with(ColumnVerdict.INCLUDED):
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

        withheld = ColumnRegistry().columns_with(ColumnVerdict.WITHHELD)
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

        gone = sorted(ColumnRegistry().known_columns() - live)
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
        import re

        # The statements moved to the database layer; the classification
        # they are checked against stayed in the service. This reads the
        # queries.
        queried = {table for source in _export_sources() for table in re.findall(r'FROM (\w+)', source)}
        claimed = {col.split('.')[0] for col in ColumnRegistry().columns_with(ColumnVerdict.INCLUDED)}

        assert queried, 'no SQL found in the service — has it changed shape?'
        missing = sorted(claimed - queried)
        assert not missing, (
            f'these tables are marked `included` in PERSONAL_DATA_COLUMNS and the export '
            f'never reads them: {missing}. Either query them or change the verdict.'
        )

    def test_credentials_are_marked_withheld_not_included(self):
        """The two that must never be exported, asserted by name so a
        careless edit to the list is caught rather than shipped."""
        registry = ColumnRegistry()
        assert registry.verdict_of('users.hashed_password') is ColumnVerdict.WITHHELD
        assert registry.verdict_of('user_roles.email_verification_token') is ColumnVerdict.WITHHELD

    def test_the_withheld_reasons_are_the_ones_the_subject_is_given(self):
        """Legal text, pinned by count and key. Moving the reasons onto
        their columns must not lose or invent one."""
        reasons = ColumnRegistry().withheld_reasons()

        assert len(reasons) == 18, sorted(reasons)
        assert set(reasons) == ColumnRegistry().columns_with(ColumnVerdict.WITHHELD)
        assert reasons['users.hashed_password'].startswith('A password hash, excluded deliberately.')


class TestAColumnClassification:
    """A reason when, and only when, the column is withheld."""

    def test_a_withheld_column_without_a_reason_is_rejected(self):
        with pytest.raises(ValidationError):
            ColumnClassification(verdict=ColumnVerdict.WITHHELD)

    def test_an_included_column_with_a_reason_is_rejected(self):
        with pytest.raises(ValidationError):
            ColumnClassification(verdict=ColumnVerdict.INCLUDED, reason='because')

    def test_the_verdict_compares_equal_to_its_word(self):
        assert ColumnVerdict.INCLUDED == 'included'


class TestTheArticle15Context:
    """Rows alone answer half the article."""

    def test_it_states_purposes_categories_recipients_retention_and_source(self):
        processing = ProcessingDescription().describe()

        for required in ('purposes', 'categories', 'recipients', 'retention', 'source'):
            assert getattr(processing, required), f'the report has no {required}'
        assert 'self-hosted' in processing.controller

    def test_retention_keeps_its_wire_spelling(self):
        """`everything else`, with the space, as it always went out."""
        retention = ProcessingDescription().describe().model_dump(by_alias=True)['retention']

        assert set(retention) == {'audit_events', 'everything else'}

    def test_recipients_reflect_what_is_configured(self, monkeypatch):
        """Generated, not boilerplate. A deployment with no relay and no
        bucket sends nothing anywhere, and the report should say so rather
        than list recipients it does not have."""

        monkeypatch.setattr(settings, 'email_enabled', False)
        monkeypatch.setattr(settings, 'smtp_host', '')

        recipients = ProcessingDescription().describe().recipients

        assert len(recipients) == 1
        assert 'none' in recipients[0].lower()

    def test_a_configured_relay_appears_as_a_recipient(self, monkeypatch):
        monkeypatch.setattr(settings, 'email_enabled', True)
        monkeypatch.setattr(settings, 'smtp_host', 'mail.corp.internal')

        recipients = ProcessingDescription().describe().recipients

        assert any('mail.corp.internal' in r for r in recipients), recipients

    def test_the_ses_api_backend_is_a_recipient_with_no_smtp_host(self, monkeypatch):
        """EMAIL_BACKEND=ses sends with no SMTP_HOST. The report decided
        "is email configured" by the host, so an SES deployment's export
        said its data went nowhere while every code went to Amazon."""

        monkeypatch.setattr(settings, 'email_enabled', True)
        monkeypatch.setattr(settings, 'email_backend', 'ses')
        monkeypatch.setattr(settings, 'smtp_host', '')
        monkeypatch.setattr(settings, 'ses_region', 'eu-west-1')

        recipients = ProcessingDescription().describe().recipients

        assert any('Amazon SES in eu-west-1' in r for r in recipients), recipients
        assert not any('none' in r.lower() for r in recipients), recipients

    def test_injected_settings_decide_whether_email_is_a_recipient(self, monkeypatch):
        """The default `EmailDelivery` shares the injected settings. It used
        to read the process-wide ones, so a description built with email
        off still listed the relay the global settings named."""

        monkeypatch.setattr(settings, 'email_enabled', True)
        monkeypatch.setattr(settings, 'smtp_host', 'mail.corp.internal')
        email_off = settings.model_copy(update={'email_enabled': False})

        recipients = ProcessingDescription(settings=email_off).describe().recipients

        assert len(recipients) == 1
        assert 'none' in recipients[0].lower()

    def test_retention_reports_the_window_actually_in_force(self, monkeypatch):
        monkeypatch.setattr(settings, 'audit_retention_days', 0)
        assert 'indefinitely' in ProcessingDescription().describe().retention.audit_events

        monkeypatch.setattr(settings, 'audit_retention_days', 365)
        assert '365 days' == ProcessingDescription().describe().retention.audit_events


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
        assert report.sections.account, report.sections
        assert report.sections.account[0].email == person['email']
        assert report.sections.organisation_memberships, report.sections

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

        assert report.sections.account == []
        assert 'No user account exists' in report.sections.note
        assert report.processing, 'the Article 15 context is missing'

    async def test_it_refuses_something_that_is_not_an_address(self):
        for bad in ('', 'jane', 'not-an-email'):
            with pytest.raises(InvalidSubjectAddress):
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
        routes = [row.route for row in report.sections.own_activity]
        assert '/v1/portal/erasure' in routes, report.sections.own_activity

        # What it says about the other person is not.
        blob = json.dumps(report, default=str)
        assert 'v***@example.com' not in blob
        assert 'detail' not in report.sections.own_activity[0].model_dump()
        # And its absence is declared, not silent.
        assert 'audit_events.detail' in report.withheld

    async def test_what_they_did_in_their_organisation_is_read_from_its_own_database(self, person):
        """Their organisation's trail is in its own database, not the shared plane's."""
        await put_in_org(
            person['org_id'],
            AuditEvent(
                id=str(uuid.uuid4()),
                occurred_at=datetime.now(timezone.utc),
                org_id=person['org_id'],
                actor_user_id=person['user_id'],
                method='POST',
                route='/v1/portal/groups',
                status_code=201,
                outcome='success',
            ),
        )

        report = await SubjectAccessService().export(email=person['email'], org_id=person['org_id'])

        assert [row.route for row in report.sections.own_activity] == ['/v1/portal/groups']

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
        attempts = report.sections.authentication_attempts

        assert attempts, 'the attempt was not returned at all'
        assert attempts[0].client_host == '10.0.0.7'
        assert attempts[0].forwarded_for == '203.0.113.9'
        assert attempts[0].user_agent == 'Mozilla/5.0 (a browser they used)'

    async def test_without_an_organisation_only_the_deployment_wide_sections_are_read(self, person):
        """An administrator with no organisation reads nothing that is an
        organisation's to disclose, and the report says so."""
        report = await SubjectAccessService(services=_RecordingServices()).export(email=person['email'])

        assert list(report.sections.model_dump(exclude_none=True)) == [
            'account',
            'deployment_role',
            'organisation_memberships',
            'organisations_owned',
            'authentication_attempts',
        ]
        assert report.scope == _DEPLOYMENT_SCOPE

    async def test_with_an_organisation_every_section_is_read_in_the_order_it_always_went_out(self, person):
        """And a services answer with nothing in it leaves `services` out
        rather than sending an empty map."""
        report = await SubjectAccessService(services=_RecordingServices()).export(
            email=person['email'], org_id=person['org_id']
        )

        wire = report.model_dump(mode='json', by_alias=True)
        assert list(wire['sections']) == [
            'account',
            'deployment_role',
            'organisation_memberships',
            'organisations_owned',
            'group_memberships',
            'model_usage',
            'own_activity',
            'things_they_created',
            'authentication_attempts',
        ]
        assert wire['sections']['account'][0]['email'] == person['email']
        assert wire['scope'] == _ORGANISATION_SCOPE.format(org_id=person['org_id'])


class TestTheEndpoint:
    @pytest.fixture
    async def admin(self, db):
        user = await create_user(db, email=f'admin-{uuid.uuid4().hex[:6]}@example.com')
        org = await create_org(db, owner_user_id=str(user.user_id), slug=f'a-{uuid.uuid4().hex[:6]}')
        await add_member(db, str(org.id), str(user.user_id), role='admin')
        return {
            'token': make_token(str(user.user_id), org_id=str(org.id), org_name='A'),
            'email': user.email,
            'user_id': str(user.user_id),
        }

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
        assert set(body['processing']['retention']) == {'audit_events', 'everything else'}
        # Sections the export did not produce are absent, not null.
        assert set(body['sections']) == {'account', 'note', 'authentication_attempts'}, body['sections']

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

    async def test_the_audit_detail_counts_the_sections_that_were_sent(self, client, admin):
        """Asked about the administrator themselves: an account, and the
        organisation's sections."""
        response = await client.post(
            '/portal/subject-access',
            headers=auth_header(admin['token']),
            json={'email': admin['email']},
        )

        assert response.status_code == 200, response.text
        sent = response.json()['sections']
        assert 'group_memberships' in sent, sent
        (row,) = audit_rows(route='/v1/portal/subject-access')
        assert row['detail']['subject_access'] == {
            'subject': f'{admin["email"][0]}***@example.com',
            'sections': len(sent),
            'has_account': True,
        }

    async def test_an_address_with_no_account_is_audited_as_such(self, client, admin):
        await client.post(
            '/portal/subject-access',
            headers=auth_header(admin['token']),
            json={'email': 'nobody@example.com'},
        )

        (row,) = audit_rows(route='/v1/portal/subject-access')
        assert row['detail']['subject_access'] == {'subject': 'n***@example.com', 'sections': 3, 'has_account': False}

    async def test_it_is_logged_with_the_address_masked(self, client, admin, caplog):
        caplog.set_level('INFO', logger='app.api.base_v1.endpoints.portal.subject_access')

        await client.post(
            '/portal/subject-access',
            headers=auth_header(admin['token']),
            json={'email': 'logged@example.com'},
        )

        assert f'Subject access export by {admin["user_id"]} for l***@example.com (3 sections)' in caplog.text
        assert 'logged@example.com' not in caplog.text

    async def test_something_that_is_not_an_address_is_refused_before_anything_is_read(self, client, admin):
        response = await client.post(
            '/portal/subject-access',
            headers=auth_header(admin['token']),
            json={'email': 'not-an-email'},
        )

        assert response.status_code == 422, response.text

    async def test_the_withheld_columns_go_out_in_their_order(self, client, admin):
        response = await client.post(
            '/portal/subject-access',
            headers=auth_header(admin['token']),
            json={'email': 'anybody@example.com'},
        )

        assert list(response.json()['withheld'].items()) == list(_WITHHELD_AS_IT_ALWAYS_WENT_OUT.items())

    def test_the_published_schema_declares_every_part_of_the_report(self, app):
        """The one contract a regulator might read is described in full:
        the context, its retention under its wire spelling, and each
        section by name, with nothing undeclared."""
        schemas = app.openapi()['components']['schemas']
        report = schemas['SubjectAccessReport']

        assert report['properties']['processing'] == {'$ref': '#/components/schemas/ProcessingContext'}
        assert report['properties']['sections'] == {'$ref': '#/components/schemas/SubjectAccessSections'}
        assert schemas['ProcessingContext']['properties']['retention'] == {'$ref': '#/components/schemas/Retention'}
        assert schemas['Retention']['required'] == ['audit_events', 'everything else']
        assert list(schemas['SubjectAccessSections']['properties']) == list(SubjectAccessSections.model_fields)
        for name in ('SubjectAccessReport', 'ProcessingContext', 'Retention', 'SubjectAccessSections'):
            assert schemas[name]['additionalProperties'] is False, name


#: The `SubjectAccessDB` reads that take a cap, found from their
#: signatures rather than written down a second time.
_CAPPED_READS = {
    name: inspect.signature(method).parameters['limit'].default
    for name, method in inspect.getmembers(SubjectAccessDB, inspect.iscoroutinefunction)
    if not name.startswith('_') and 'limit' in inspect.signature(method).parameters
}


_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _account(*, email: str = 'someone@example.com') -> AccountRow:
    return AccountRow(
        user_id='u-1',
        email=email,
        full_name=None,
        auth_provider='local',
        created_at=_AT,
        updated_at=_AT,
        last_sign_in_at=None,
        tokens_valid_after=None,
        deactivated_at=None,
        is_super_admin=False,
    )


def _audit_events(n: int) -> list[AuditEventRow]:
    return [
        AuditEventRow(
            occurred_at=_AT,
            method='GET',
            route=f'/v1/{i}',
            target_type=None,
            target_id=None,
            status_code=200,
            outcome='allowed',
            client_host=None,
            forwarded_for=None,
            user_agent=None,
        )
        for i in range(n)
    ]


def _model_usage(n: int) -> list[ModelUsageRow]:
    return [
        ModelUsageRow(
            org_id='org-1',
            model='m',
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
            duration=None,
            stream=0,
            created_at=_AT,
        )
        for _ in range(n)
    ]


class _FakeDB:
    """Every read `SubjectAccessDB` offers, answering for one account.

    Each capped read returns exactly its cap, so every one of them should
    be reported truncated.
    """

    def __init__(self, *, email: str = 'someone@example.com') -> None:
        self._email = email

    async def account(self, email: str) -> list[AccountRow]:
        return [_account(email=self._email)]

    async def deployment_role(self, user_id: str) -> list:
        return []

    async def organisation_memberships(self, user_id: str) -> list:
        return []

    async def organisations_owned(self, user_id: str) -> list:
        return []

    async def group_memberships(self, org_id: str, *, user_id: str) -> list:
        return []

    async def things_they_created(self, *, org_id: str, user_id: str) -> ThingsTheyCreated:
        return ThingsTheyCreated.model_validate({name: [] for name in ThingsTheyCreated.model_fields})

    async def model_usage(self, org_id: str, *, user_id: str) -> list[ModelUsageRow]:
        return _model_usage(_CAPPED_READS['model_usage'])

    async def own_activity(self, org_id: str, *, user_id: str) -> list[AuditEventRow]:
        return _audit_events(_CAPPED_READS['own_activity'])

    async def authentication_attempts(self, org_id: str | None, *, email: str) -> list[AuditEventRow]:
        return _audit_events(_CAPPED_READS['authentication_attempts'])


class _FakeServices:
    def __init__(self, *, missing: list[str] | None = None) -> None:
        self._missing = missing or []

    async def export(self, user_id: str, *, org_id: str) -> ExportAnswers:
        return ExportAnswers(missing=self._missing)


def _builder() -> SubjectAccessReportBuilder:
    return SubjectAccessReportBuilder(
        subject='someone@example.com',
        generated_at=datetime.now(timezone.utc),
        processing=ProcessingDescription().describe(),
        withheld={},
    )


class TestATruncatedReportSaysSo:
    """`no silent caps`. This export is capped per section, and a cap that
    is not reported turns an incomplete legal answer into one that reads
    as complete — worse than refusing."""

    def test_a_full_section_is_declared_truncated(self):
        report = _builder()
        report.section(Section.OWN_ACTIVITY, rows=_audit_events(ACTIVITY_LIMIT), cap=ACTIVITY_LIMIT)

        truncated = report.build().truncated
        assert truncated is not None
        assert 'There are more' in truncated['own_activity']

    def test_a_short_section_is_not(self):
        """Otherwise every export claims truncation and the flag means
        nothing."""
        report = _builder()
        report.section(Section.OWN_ACTIVITY, rows=_audit_events(3), cap=ACTIVITY_LIMIT)

        assert report.build().truncated is None

    async def test_every_capped_query_reports_its_cap(self):
        """The rule, not the call sites. A read added later with a `limit`
        and no disclosure fails here — and so does a service whose cap
        has drifted from the query's."""
        assert _CAPPED_READS, 'no SubjectAccessDB read takes a limit — has its shape changed?'

        report = await SubjectAccessService(db=_FakeDB(), services=_FakeServices()).export(
            email='someone@example.com', org_id='org-1'
        )

        assert set(report.truncated or {}) == set(_CAPPED_READS), (
            'every read that carries a cap must report hitting it. A section that silently '
            'stops at its cap tells the subject they have all their data when they do not.'
        )


class TestTheBuilderOwnsTheSections:
    """The builder is the only writer of the sections, and each write
    says what it is: the account, one read's rows, or the things made."""

    def test_no_account_is_said_and_answers_no_user_id(self):
        report = _builder()

        assert report.account([]) is None

        sections = report.build().sections
        assert sections.account == []
        assert sections.note is not None and 'No user account exists' in sections.note

    def test_an_account_answers_its_user_id(self):
        report = _builder()

        assert report.account([_account()]) == 'u-1'
        assert report.build().sections.note is None

    def test_every_section_name_is_a_field_of_the_sections(self):
        """The values double as `truncated` wire keys, so they are pinned
        to the model's field names."""
        assert {section.value for section in Section} <= set(SubjectAccessSections.model_fields)

    def test_every_capped_read_is_a_section(self):
        """A capped read that is not a `Section` could not report its cap."""
        assert set(_CAPPED_READS) <= {section.value for section in Section}

    def test_an_uncapped_section_is_never_truncated(self):
        report = _builder()
        report.section(
            Section.GROUP_MEMBERSHIPS,
            rows=[GroupMembershipRow(group_id='g', added_by_user_id=None, added_at=_AT)] * ACTIVITY_LIMIT,
        )

        assert report.build().truncated is None


class TestThingsTheyCreated:
    """A fixed key set, so a model — and one that refuses to be incomplete."""

    def test_a_missing_group_is_refused(self):
        groups = {name: [] for name in ThingsTheyCreated.model_fields if name != 'inference_nodes'}

        with pytest.raises(ValidationError):
            ThingsTheyCreated.model_validate(groups)

    def test_the_wire_order_is_the_order_the_database_reads_them(self):
        """It was a dict built in this order; the JSON must not change."""
        source = inspect.getsource(CreatedThingsDB.things_they_created)
        read = re.findall(r'^\s+(\w+)=await self\._', source, flags=re.M)
        things = ThingsTheyCreated.model_validate({name: [] for name in ThingsTheyCreated.model_fields})

        assert read, 'no groups parsed from CreatedThingsDB.things_they_created — has it changed shape?'
        assert list(things.model_dump(mode='json')) == read


class TestTheEnvelopeCarriesEverythingTheExportSets:
    """`SubjectAccessReport` is a model, and a model drops what it does
    not declare.

    On an Article 15 answer that is not a typing detail. The first
    version of this model would have silently dropped `truncated` — the
    key whose entire job is to say the answer is incomplete. So the
    envelope and its sections refuse anything undeclared, and an export
    that sets every optional part is shown to carry each one out.
    """

    def test_the_envelope_and_its_sections_refuse_undeclared_fields(self):
        assert SubjectAccessReport.model_config.get('extra') == 'forbid'
        assert SubjectAccessSections.model_config.get('extra') == 'forbid'
        with pytest.raises(ValueError):
            SubjectAccessSections().own_actvity = []  # type: ignore[attr-defined]

    async def test_truncated_and_scope_survive_to_the_wire(self):
        report = await SubjectAccessService(db=_FakeDB(), services=_FakeServices(missing=['CodeIndex'])).export(
            email='someone@example.com', org_id='org-1'
        )

        wire = report.model_dump(mode='json')
        assert 'There are more' in wire['truncated']['own_activity']
        assert 'CodeIndex did not answer' in wire['truncated']['CodeIndex']
        assert 'organisation org-1' in wire['scope']


class TestEachServiceUsesItsOwnDatabase:
    """`SubjectAccessService` was a `SingletonMeta` singleton, so the
    `db` passed to every construction after the first was silently
    ignored and a test's fake could read another test's database."""

    async def test_two_services_read_two_databases(self):
        first = SubjectAccessService(db=_FakeDB(email='first@example.com'), services=_FakeServices())
        second = SubjectAccessService(db=_FakeDB(email='second@example.com'), services=_FakeServices())

        a = await first.export(email='x@example.com')
        b = await second.export(email='x@example.com')

        assert a.sections.account == [_account(email='first@example.com')]
        assert b.sections.account == [_account(email='second@example.com')]


_WITHHELD_AS_IT_ALWAYS_WENT_OUT = {
    'users.hashed_password': (
        'A password hash, excluded deliberately. It is not meaningful personal data and '
        'disclosing it would help an attacker who persuaded an administrator to run this '
        'export.'
    ),
    'user_roles.email_verification_token': ('A single-use token, excluded for the same reason.'),
    'registered_clients.decided_by_user_id': (
        'Which super admin approved or revoked a service registered with this deployment. The '
        'same decision is in your own audit activity above.'
    ),
    'extension_priority.set_by_user_id': (
        'Which super admin set the priority order of extensions, which decides which of two '
        'has a contested part of igni. The same change is in your own audit activity above.'
    ),
    'service_attachments.content': (
        'The bytes of files a service sent your agent. They are described above; the bytes '
        "were the service's to send, are kept at most 14 days, and the copy imported into your "
        'chat is in your igni.'
    ),
    'vm_credentials.secret_hash': (
        "Hashes of your cloud VM's credentials. A hash cannot be used to sign in, but it is a "
        'credential check, not information about you.'
    ),
    'vm_credentials.previous_hashes': (
        "Hashes of your cloud VM's earlier credentials, kept to notice one being reused. Withheld for the same reason."
    ),
    'api_keys.secret_hash': (
        'Hashes of your API keys. A hash cannot be used to act as you, but it is a credential '
        'check, not information about you.'
    ),
    'producer_feed_marks.producer_id': (
        "That something about you changed and the cloud's gateway has yet to be told: kept for "
        "seconds, until its next poll, and saying nothing the gateway's feed above does not."
    ),
    'platform_vm_caps.updated_by_user_id': (
        "Which super admin set the platform's caps on VM profiles. The same change is in your own audit activity above."
    ),
    'org_vm_quotas.updated_by_user_id': (
        "Which super admin set an organisation's quota of awake VM time. The same change is in "
        'your own audit activity above.'
    ),
    'producer_exports.content_oid': (
        'An archive of your data in an organisation you were removed from, exported for that '
        'organisation, which controls it, during its retention. Ask the organisation for it; '
        'it is deleted when it expires.'
    ),
    'platform_retention.updated_by_user_id': (
        "Which super admin set the platform's longest retention of removed members. The same "
        'change is in your own audit activity above.'
    ),
    'org_retention.updated_by_user_id': (
        "Which admin set an organisation's retention of removed members. The same change is in "
        'your own audit activity above.'
    ),
    'platform_space_caps.updated_by_user_id': (
        "Which super admin set the platform's caps on apps' spaces. The same change is in your "
        'own audit activity above.'
    ),
    'org_space_quotas.updated_by_user_id': (
        "Which admin set an organisation's limits on apps' spaces. The same change is in your own audit activity above."
    ),
    'app_spaces.external_id': (
        "An app's own name for one of its users. It never names an igni account, so nothing of "
        "yours is in it; the app's operator answers for its users."
    ),
    'audit_events.detail': (
        'Per-route detail attached to your audit records. Withheld because on some routes it '
        'describes a third party rather than you — an erasure you performed carries the '
        'address of the person erased, an access review carries the member reviewed. The shape '
        'differs per route, so there is no single rule for what to remove, and returning it '
        "would disclose other people's data in the act of answering your request. Ask the "
        'controller if you need a specific record explained.'
    ),
}


_DEPLOYMENT_SCOPE = (
    'Prepared without an organisation, so only records that are not '
    "organisation-specific are included. Each organisation's own records — groups, "
    'usage, activity — are not.'
)

_ORGANISATION_SCOPE = (
    'Prepared by an administrator of organisation {org_id}, from its records. '
    'Sections that are not organisation-specific — the account and sign-in '
    'attempts — describe the person across this deployment. Another '
    "organisation's records about the same address are not included: they are "
    "that organisation's to disclose."
)


class _RecordingServices:
    """The registered services, answering `sections`, and noting who asked."""

    def __init__(self, *, sections: dict | None = None, missing: list[str] | None = None) -> None:
        self._sections = sections or {}
        self._missing = missing or []
        self.asked: list[tuple[str, str]] = []

    async def export(self, user_id: str, *, org_id: str) -> ExportAnswers:
        self.asked.append((user_id, org_id))
        return ExportAnswers(sections=self._sections, missing=self._missing)


class _NoAccountDB(_FakeDB):
    async def account(self, email: str) -> list[AccountRow]:
        return []


class TestTheWithheldColumnsGoOutAsTheyAlwaysDid:
    """The reasons moved onto their columns, which are listed table by
    table. The report's `withheld` keeps the order and the words the
    export wrote by hand — the JSON must not change shape."""

    def test_in_the_same_order_and_the_same_words(self):
        reasons = ColumnRegistry().withheld_reasons()

        assert list(reasons.items()) == list(_WITHHELD_AS_IT_ALWAYS_WENT_OUT.items())

    def test_a_withheld_column_added_later_follows_the_others(self):
        personal = {
            'new_table.secret': ColumnClassification.withheld('A new secret.'),
            **ColumnRegistry().personal(),
        }

        reasons = ColumnRegistry(personal=personal).withheld_reasons()

        assert list(reasons) == [*_WITHHELD_AS_IT_ALWAYS_WENT_OUT, 'new_table.secret']

    def test_a_column_that_is_not_personal_has_no_verdict(self):
        registry = ColumnRegistry()

        assert registry.verdict_of('users.email') is ColumnVerdict.INCLUDED
        assert registry.verdict_of('org_invitations.email') is ColumnVerdict.DEAD
        assert registry.verdict_of(next(iter(registry.not_personal()))) is None
        assert registry.verdict_of('no_such_table.column') is None


class TestTheContextIsWordedAsItAlwaysWas:
    """What the subject reads about the processing, word for word."""

    def test_the_fixed_sentences(self):
        processing = ProcessingDescription().describe()

        assert processing.controller == (
            'The organisation operating this igni deployment. igni is self-hosted: '
            'the software vendor holds none of this data and has no access to it.'
        )
        assert processing.purposes == [
            'authenticating the person and deciding what they may access',
            'recording who performed privileged actions, for security review',
            'attributing group configuration to whoever made it',
            'accounting for model usage',
        ]
        assert processing.categories == [
            'identity and contact data (name, email address)',
            'authentication metadata (method, last sign-in)',
            'authorisation data (organisation membership, role, status)',
            'activity records (audit events, model usage)',
        ]
        assert processing.source == (
            'Provided by the person when they signed up or accepted an invitation, '
            'or by an administrator who invited them.'
        )
        assert processing.rights == (
            'Rectification and erasure are available; erasure via POST /v1/portal/erasure (GDPR Art. 17).'
        )
        assert processing.retention.everything_else == (
            'kept until the account or the organisation is deleted, or the person exercises erasure'
        )

    def test_ses_with_no_region_names_the_sdk_default(self, monkeypatch):
        monkeypatch.setattr(settings, 'email_enabled', True)
        monkeypatch.setattr(settings, 'email_backend', 'ses')
        monkeypatch.setattr(settings, 'ses_region', '')

        recipients = ProcessingDescription().describe().recipients

        assert recipients == ["Amazon SES in the AWS SDK's default region, for sign-in codes and invitations"]

    def test_an_smtp_relay_is_named_by_its_host(self, monkeypatch):
        monkeypatch.setattr(settings, 'email_enabled', True)
        monkeypatch.setattr(settings, 'email_backend', 'smtp')
        monkeypatch.setattr(settings, 'smtp_host', 'mail.corp.internal')

        recipients = ProcessingDescription().describe().recipients

        assert recipients == ['the SMTP relay at mail.corp.internal, for sign-in codes and invitations']


class TestTheReportSaysWhoseRecordsTheseAre:
    def test_a_deployment_wide_report_says_what_it_leaves_out(self):
        report = _builder()
        report.scoped_to(None)

        assert report.build().scope == _DEPLOYMENT_SCOPE

    def test_an_organisations_report_says_it_is_that_organisations(self):
        report = _builder()
        report.scoped_to('org-1')

        assert report.build().scope == _ORGANISATION_SCOPE.format(org_id='org-1')

    async def test_truncated_keeps_the_order_the_export_found_them_in(self):
        report = await SubjectAccessService(db=_FakeDB(), services=_RecordingServices(missing=['CodeIndex'])).export(
            email='someone@example.com', org_id='org-1'
        )

        assert list(report.truncated or {}) == ['model_usage', 'own_activity', 'CodeIndex', 'authentication_attempts']
        assert report.truncated is not None
        assert report.truncated['model_usage'] == (
            f'{ACTIVITY_LIMIT} rows returned, which is the maximum this export produces. '
            'There are more. Narrow the request or query the database directly '
            'for the complete set.'
        )
        assert report.truncated['CodeIndex'] == (
            'CodeIndex did not answer; what it holds about this person is missing from this '
            'export. Ask again, or ask an operator to check it.'
        )


class TestWhatTheServicesAreAsked:
    async def test_a_service_that_answered_is_under_its_name(self):
        answered = {'CodeIndex': {'repositories': [{'name': 'igni'}]}}

        report = await SubjectAccessService(db=_FakeDB(), services=_RecordingServices(sections=answered)).export(
            email='someone@example.com', org_id='org-1'
        )

        assert report.sections.services == answered
        assert list(report.model_dump(mode='json')['sections'])[-2:] == ['services', 'authentication_attempts']

    async def test_nobody_is_asked_about_an_address_with_no_account(self):
        services = _RecordingServices()

        report = await SubjectAccessService(db=_NoAccountDB(), services=services).export(
            email='someone@example.com', org_id='org-1'
        )

        assert services.asked == []
        assert list(report.sections.model_dump(exclude_none=True)) == ['account', 'note', 'authentication_attempts']

    async def test_nobody_is_asked_without_an_organisation(self):
        services = _RecordingServices()

        await SubjectAccessService(db=_FakeDB(), services=services).export(email='someone@example.com')

        assert services.asked == []

    async def test_an_account_in_an_organisation_is_asked_about_by_its_user_id(self):
        services = _RecordingServices()

        await SubjectAccessService(db=_FakeDB(), services=services).export(email='someone@example.com', org_id='org-1')

        assert services.asked == [('u-1', 'org-1')]


class TestTheSectionsCountAndOrder:
    """What the audit trail records about an export, and the order the
    sections go out in."""

    def test_nothing_produced_counts_nothing(self):
        sections = SubjectAccessSections()

        assert sections.present_count() == 0
        assert not sections.has_account()

    def test_an_empty_account_and_its_note_both_count(self):
        """The old count was the number of keys written, and an empty
        account list was a key."""
        sections = SubjectAccessSections(account=[], note='none', authentication_attempts=[])

        assert sections.present_count() == 3
        assert not sections.has_account()

    def test_an_account_with_a_row_is_an_account(self):
        assert SubjectAccessSections(account=[_account()]).has_account()

    def test_the_sections_go_out_in_the_order_the_export_writes_them(self):
        every = SubjectAccessSections(
            authentication_attempts=[],
            services={},
            things_they_created=ThingsTheyCreated.model_validate({n: [] for n in ThingsTheyCreated.model_fields}),
            own_activity=[],
            model_usage=[],
            group_memberships=[],
            organisations_owned=[],
            organisation_memberships=[],
            deployment_role=[],
            note='n',
            account=[],
        )

        assert list(every.model_dump(mode='json')) == [
            'account',
            'note',
            'deployment_role',
            'organisation_memberships',
            'organisations_owned',
            'group_memberships',
            'model_usage',
            'own_activity',
            'things_they_created',
            'services',
            'authentication_attempts',
        ]
        assert every.present_count() == 11


class TestBothTrailsAreRead:
    """The deployment's audit trail and the organisation's, merged newest
    first and cut at the cap."""

    @pytest.fixture
    async def member(self, db):
        user = await create_user(db, email=f'trails-{uuid.uuid4().hex[:8]}@example.com')
        org = await create_org(db, owner_user_id=str(user.user_id), slug=f't-{uuid.uuid4().hex[:6]}')
        return {'email': user.email, 'user_id': str(user.user_id), 'org_id': str(org.id)}

    @staticmethod
    def _in_the_deployment(member: dict, route: str, at: datetime) -> None:
        _execute(
            'INSERT INTO audit_events (id, occurred_at, actor_user_id, actor_email, method, route, '
            "outcome, status_code) VALUES (:i, :at, :u, :e, 'POST', :r, 'allowed', 200)",
            {'i': str(uuid.uuid4()), 'at': at, 'u': member['user_id'], 'e': member['email'], 'r': route},
        )

    @staticmethod
    async def _in_the_organisation(member: dict, route: str, at: datetime) -> None:
        await put_in_org(
            member['org_id'],
            AuditEvent(
                id=str(uuid.uuid4()),
                occurred_at=at,
                actor_user_id=member['user_id'],
                actor_email=member['email'],
                method='POST',
                route=route,
                outcome='allowed',
                status_code=200,
            ),
        )

    async def _four_events(self, member: dict) -> None:
        self._in_the_deployment(member, '/v1/first', datetime(2026, 1, 1, tzinfo=timezone.utc))
        await self._in_the_organisation(member, '/v1/second', datetime(2026, 1, 2, tzinfo=timezone.utc))
        self._in_the_deployment(member, '/v1/third', datetime(2026, 1, 3, tzinfo=timezone.utc))
        await self._in_the_organisation(member, '/v1/fourth', datetime(2026, 1, 4, tzinfo=timezone.utc))

    async def test_own_activity_is_newest_first_across_both_and_stops_at_the_cap(self, member):
        await self._four_events(member)

        rows = await SubjectAccessDB().own_activity(member['org_id'], user_id=member['user_id'], limit=3)

        assert [row.route for row in rows] == ['/v1/fourth', '/v1/third', '/v1/second']

    async def test_authentication_attempts_read_both_with_an_organisation(self, member):
        await self._four_events(member)

        rows = await SubjectAccessDB().authentication_attempts(member['org_id'], email=member['email'].upper())

        assert [row.route for row in rows] == ['/v1/fourth', '/v1/third', '/v1/second', '/v1/first']

    async def test_authentication_attempts_without_an_organisation_read_only_the_deployments(self, member):
        await self._four_events(member)

        rows = await SubjectAccessDB().authentication_attempts(None, email=member['email'], limit=1)

        assert [row.route for row in rows] == ['/v1/third']

    async def test_their_organisations_reads_are_its_own(self, member):
        await put_in_org(
            member['org_id'],
            OrgGroup(id='g-1', org_id=member['org_id'], name='Legal', is_default=False, created_at=_AT, updated_at=_AT),
            OrgGroupMember(id=str(uuid.uuid4()), group_id='g-1', user_id=member['user_id'], added_at=_AT),
        )

        db = SubjectAccessDB()
        groups = await db.group_memberships(member['org_id'], user_id=member['user_id'])

        assert [g.group_id for g in groups] == ['g-1']
        assert await db.model_usage(member['org_id'], user_id=member['user_id']) == []
