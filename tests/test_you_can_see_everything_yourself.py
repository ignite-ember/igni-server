"""Structured logs and a bulk audit export: the means to look, on your side.

F102. F101 gave a deployment numbers to alert on. This is the rest of what
"see whatever you want" needs, and none of it sends anything anywhere:

* `LOG_FORMAT=json` — one object per line, so a shipper can filter on the
  level, the module and which repository a task was working on, instead of
  a regular expression over a rendered string;
* `GET /v1/portal/audit-events/export` — the audit trail as JSON Lines,
  oldest first, resumable. LG-7's gap named "no SIEM integration", and the
  reviewable page it already had does not close it: nobody loads a year of
  records into Splunk 100 rows at a time through a UI envelope.

The formatter's field list is the security property here, so most of these
tests are about what does *not* come out.
"""

from __future__ import annotations

import json
import logging
import pathlib
import uuid

import pytest
from sqlalchemy import create_engine, text

from app.api.base_v1.schema.portal.user import PortalUser
from app.observability.log_format import _FIELDS, JsonFormatter
from tests.conftest import add_member, audit_rows, auth_header, create_org, create_user, make_token, TEST_DB_URL_SYNC

_ROOT = pathlib.Path(__file__).resolve().parent.parent

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


def _emit(**kwargs) -> dict:
    """Format one record through the real formatter and parse it back."""
    record = logging.LogRecord(
        name=kwargs.pop('name', 'app.demo'),
        level=kwargs.pop('level', logging.INFO),
        pathname='/app/demo.py',
        lineno=1,
        msg=kwargs.pop('msg', 'hello'),
        args=kwargs.pop('args', ()),
        exc_info=kwargs.pop('exc_info', None),
    )
    for key, value in kwargs.items():
        setattr(record, key, value)
    return json.loads(JsonFormatter().format(record))


class TestTheJsonFormatIsQueryable:
    def test_it_emits_one_object_per_record(self):
        line = JsonFormatter().format(logging.LogRecord('app.demo', logging.INFO, '/x.py', 1, 'hello', (), None))

        assert '\n' not in line, 'a multi-line record breaks every line-oriented shipper'
        assert json.loads(line)['message'] == 'hello'

    def test_the_timestamp_is_utc_iso_with_milliseconds(self):
        """The text format shipped a literal `.%f` on its first production
        boot, because `datefmt` goes to `time.strftime`, which has no
        `%f`. This is the same mistake's other half."""
        ts = _emit()['ts']

        assert ts.endswith('Z'), ts
        assert '.' in ts and len(ts.split('.')[1]) == 4, f'{ts} has no milliseconds'

    def test_arguments_are_interpolated(self):
        """Otherwise every parameterised line arrives as a format string
        with the values in a field nobody joins back up."""
        assert _emit(msg='took %s ms for %s', args=(12, 'repo'))['message'] == 'took 12 ms for repo'

    def test_the_task_context_becomes_fields(self):
        """`[repo|evt:12|abc1234]` is compact for a person and opaque to a
        query. This is the difference between "show me everything about
        this repository" being a filter and being a regex."""
        from app.logging_context import clear_task_context, set_task_context

        set_task_context(repo_name='acme/api', event_id=42, commit_sha='abcdef1234567890')
        try:
            payload = _emit()
        finally:
            clear_task_context()

        assert payload['repo'] == 'acme/api'
        assert payload['event_id'] == '42'
        assert payload['commit'] == 'abcdef1', 'the commit should be the short sha, not the whole thing'

    def test_empty_context_fields_are_omitted(self):
        """A field that is blank on 99% of lines costs an index and
        answers no question."""
        from app.logging_context import clear_task_context

        clear_task_context()
        payload = _emit()

        assert 'repo' not in payload
        assert 'commit' not in payload

    def test_an_exception_arrives_as_one_string(self):
        try:
            raise ValueError('boom')
        except ValueError:
            import sys

            payload = _emit(msg='failed', exc_info=sys.exc_info())

        assert 'ValueError: boom' in payload['exception']
        assert 'Traceback' in payload['exception']

    def test_a_callers_extra_cannot_add_a_field(self):
        """The security property. The obvious implementation dumps
        `record.__dict__`, which carries whatever anybody attached via
        `extra=` — a category that includes tokens somebody logged in a
        hurry, and one that grows with every new log line.
        """
        payload = _emit(authorization='Bearer sk-secret-value', password='hunter2')

        assert set(payload) <= set(_FIELDS), f'unexpected fields: {sorted(set(payload) - set(_FIELDS))}'
        assert 'sk-secret-value' not in json.dumps(payload)
        assert 'hunter2' not in json.dumps(payload)

    def test_non_ascii_survives(self):
        """`ensure_ascii` would render the emoji in the startup banner as
        `\\ud83d\\ude80`, which is a worse line in a log viewer."""
        assert _emit(msg='🚀 starting')['message'] == '🚀 starting'

    def test_the_default_is_still_the_human_format(self):
        """A customer who has not asked for JSON gets the format they
        already read. Switching the default would change every line of
        every existing deployment's logs on upgrade."""
        from app.config import Settings

        assert Settings.model_fields['log_format'].default == 'text'

    def test_the_format_is_selected_from_the_environment(self):
        source = (_ROOT / 'app/main.py').read_text()

        assert "os.getenv('LOG_FORMAT'" in source
        assert 'configure_logging' in source


class TestTheAuditTrailCanBeBulkExported:
    @pytest.fixture
    async def admin(self, db):
        user = await create_user(db, email=f'admin-{uuid.uuid4().hex[:6]}@example.invalid')
        org = await create_org(db, owner_user_id=str(user.user_id), slug=f'o-{uuid.uuid4().hex[:6]}')
        await add_member(db, str(org.id), str(user.user_id), role='admin')
        return {
            'token': make_token(str(user.user_id), org_id=str(org.id), org_name='O'),
            'org_id': str(org.id),
        }

    async def test_it_streams_json_lines(self, client, admin):
        response = await client.get('/portal/audit-events/export', headers=auth_header(admin['token']))

        assert response.status_code == 200, response.text
        assert response.headers['content-type'].startswith('application/x-ndjson')

        for line in response.text.splitlines():
            json.loads(line)

    async def test_it_returns_oldest_first(self, client, admin):
        """The opposite of the review page, and not a preference: a
        collector resumes from the last row it ingested, which only works
        if the order matches the cursor. Backwards, it re-reads the newest
        page forever.
        """
        # The export request itself is audited (F90), so a handful of
        # calls is enough to produce rows to order.
        for _ in range(3):
            await client.get('/portal/audit-events/export', headers=auth_header(admin['token']))

        response = await client.get('/portal/audit-events/export', headers=auth_header(admin['token']))
        stamps = [json.loads(line)['occurred_at'] for line in response.text.splitlines()]

        assert len(stamps) >= 3, f'only {len(stamps)} rows — this test is measuring nothing'
        assert stamps == sorted(stamps), 'the export is not oldest-first, so `since` cannot resume it'

    async def test_since_is_inclusive_so_a_collector_cannot_skip_a_row(self, client, admin):
        """A duplicate is harmless — the id is stable and every sane
        importer deduplicates. A gap in an audit trail is the thing the
        table exists to not have."""
        for _ in range(2):
            await client.get('/portal/audit-events/export', headers=auth_header(admin['token']))

        rows = [
            json.loads(line)
            for line in (
                await client.get('/portal/audit-events/export', headers=auth_header(admin['token']))
            ).text.splitlines()
        ]
        assert rows, 'no rows to resume from'
        cursor = rows[-1]['occurred_at']

        resumed = [
            json.loads(line)
            for line in (
                await client.get(f'/portal/audit-events/export?since={cursor}', headers=auth_header(admin['token']))
            ).text.splitlines()
        ]

        assert any(row['occurred_at'] == cursor for row in resumed), (
            '`since` excluded the cursor row, so a collector storing the last timestamp '
            'it saw would skip whatever shares that instant'
        )

    async def test_it_is_admin_only(self, client, db):
        user = await create_user(db, email=f'member-{uuid.uuid4().hex[:6]}@example.invalid')
        org = await create_org(db, owner_user_id=str(user.user_id), slug=f'm-{uuid.uuid4().hex[:6]}')
        await add_member(db, str(org.id), str(user.user_id), role='member')

        response = await client.get(
            '/portal/audit-events/export',
            headers=auth_header(make_token(str(user.user_id), org_id=str(org.id), org_name='O')),
        )

        assert response.status_code == 403, response.status_code

    async def test_it_needs_a_token_at_all(self, client):
        response = await client.get('/portal/audit-events/export')

        assert response.status_code in (401, 403), response.status_code

    async def test_the_export_is_itself_audited_with_counts_only(self, client, admin):
        """An export of the audit trail is a privileged read, and the
        record of it answers "who took a copy". Counts rather than rows:
        the trail must not become the place a copy of the trail lives.

        A GET is not audited by default — the general rule is that
        mutations are events and reads are not, because a page of groups
        is not an event. This route is in `_AUDITED_READS` instead, and
        that list is where the exception has to be argued.
        """
        await client.get('/portal/audit-events/export', headers=auth_header(admin['token']))

        # Synchronous: the `db` fixture and the `client` fixture run on
        # different event loops, and mixing them fails with "attached to
        # a different loop" rather than with anything about audit records.
        rows = audit_rows(route='/v1/portal/audit-events/export')

        assert rows, 'the export is not audited'
        assert any('exported' in str(row['detail'] or '') for row in rows), rows[0]['detail']

    async def test_the_org_scope_is_not_a_parameter(self, client, admin):
        """Same rule as the review page: `org_id` comes from the token.
        A bulk export is exactly where a cross-org read would be most
        valuable to somebody."""
        source = (_ROOT / 'app/api/base_v1/endpoints/portal/audit.py').read_text()
        export = source[source.index('async def export_audit_events') :]

        assert 'org_id=current_user.org_id' in export
        assert 'org_id: ' not in export.split('-> StreamingResponse')[0], 'org_id is a request parameter on the export'


def test_the_portal_user_schema_is_unchanged_by_any_of_this():
    """A canary: none of the observability work should have touched the
    shape of an authenticated user."""
    assert 'org_id' in PortalUser.model_fields
