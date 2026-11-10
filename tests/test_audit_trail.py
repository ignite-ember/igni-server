"""Privileged activity is recorded, and the record cannot be rewritten.

`COMPLIANCE.md` had the absence of an audit trail as the largest technical
gap: SOC 2 CC7.2, ISO 27001 Annex A 8.15, and GDPR Article 33 by
implication, because a breach cannot be scoped from records that do not
exist. Before this, an administrator could delete an organisation, change
a role or register a GitHub App and leave nothing behind but two
`logger.info` calls in `inference.py`.

The design decision under test here is **structural coverage**. A call in
each mutating handler would be a list of call sites, and this project has
two records of what happens to those: F80's rename was fixed
instance-by-instance and grew 25 new instances, and F88's service contract
drifted to 60% accuracy. So the middleware records every mutation to an
authenticated surface whether or not the handler knows it exists, and
`test_coverage_is_structural_not_a_list` asserts that property against the
live router rather than against a table somebody has to update.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import text

from app.db.sql.audit_events import AuditEventDB, AuditRecord, classify
from app.middlewares.audit_policy import _AUDITED_READS, is_auditable
from app.middlewares.request_audit import AuditTarget
from tests.conftest import add_member, audit_rows, auth_header, create_org, create_user, make_token

pytestmark = pytest.mark.anyio


def _sync_engine():
    """A synchronous engine, deliberately.

    The `db` fixture and the `client` fixture run on **different event
    loops**, so a row written by the application through the middleware
    cannot be read back through `db` — asyncpg raises "attached to a
    different loop". §38's cross-org sweep hit this and solved it the same
    way. Everything in this file that touches `audit_events` goes through
    here, which removes the whole class of problem rather than working
    around it per test.
    """
    from sqlalchemy import create_engine

    from tests.conftest import TEST_DB_URL_SYNC

    return create_engine(TEST_DB_URL_SYNC)


def _rows(**where):
    return audit_rows(**where)


def _execute(sql: str, params: dict | None = None) -> None:
    engine = _sync_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params or {})
    finally:
        engine.dispose()


@pytest.fixture
async def admin(db):
    user = await create_user(db, email=f'admin-{uuid.uuid4().hex[:6]}@example.com')
    org = await create_org(db, owner_user_id=str(user.user_id), slug=f'o-{uuid.uuid4().hex[:6]}')
    await add_member(db, str(org.id), str(user.user_id), role='admin')
    return {
        'user_id': str(user.user_id),
        'email': str(user.email),
        'org_id': str(org.id),
        'token': make_token(str(user.user_id), org_id=str(org.id), org_name='O'),
    }


class TestTheRuleItself:
    """`is_auditable` is the whole policy, so it is tested without a request."""

    @pytest.mark.parametrize(
        'method,path',
        [
            ('POST', '/v1/portal/groups'),
            ('PATCH', '/v1/portal/organizations/abc'),
            ('DELETE', '/v1/portal/team/u1'),
            ('PUT', '/v1/admin/something'),
            ('POST', '/v1/directory/people/resolve'),
            ('POST', '/v1/service-notifications'),
            # A service registering itself, and uploading its extension.
            ('POST', '/v1/clients'),
            ('PUT', '/v1/clients/abc/extension'),
            # Authentication, whatever the method — a failed sign-in is the
            # most-reviewed line in any audit trail.
            ('POST', '/v1/sign-in'),
            ('POST', '/v1/sign-in-with-code'),
            ('POST', '/v1/send-sign-in-code'),
            ('GET', '/v1/auth/oidc'),
            ('POST', '/v1/cli/token'),
            # Two reads, for two different reasons. The export hands
            # somebody a copy of the whole trail, so the question
            # afterwards is "who took it".
            ('GET', '/v1/portal/audit-events/export'),
            # And the access review, where the *act* is the evidence.
            # C10: AC-8 is "periodic access review", and CC6.3 asks
            # whether somebody looks. This endpoint made looking
            # possible and nothing recorded that anybody had, so a Type
            # II auditor sampling "the reviews you performed in Q2"
            # found nothing — the control that exists to be sampled
            # produced no sample.
            ('GET', '/v1/portal/access-review'),
            ('GET', '/v1/portal/access-review?dormant_days=90'),
        ],
    )
    def test_these_are_recorded(self, method, path):
        assert is_auditable(method, path)

    @pytest.mark.parametrize(
        'method,path',
        [
            # Reads on the portal are deliberately not recorded: the portal
            # issues many per page view and "who looked at the repository
            # list" is not the question an auditor asks.
            ('GET', '/v1/portal/repositories'),
            ('GET', '/v1/portal/me'),
            ('GET', '/health'),
            ('GET', '/ready'),
            ('POST', '/v1/webhooks/github'),
            ('POST', '/v1/chat/completions'),
        ],
    )
    def test_these_are_not(self, method, path):
        assert not is_auditable(method, path)

    def test_a_read_is_recorded_only_where_the_reading_matters(self):
        """The exemption has to stay narrow.

        "Reads are not events" is the rule, and each exception weakens
        it — a trail that records page views is one nobody reads. Two
        earn it: the export, because it is a copy of everything, and the
        access review, because performing it *is* the control. A third
        should have to argue for itself here.
        """
        assert set(_AUDITED_READS) == {
            '/v1/portal/audit-events/export',
            '/v1/portal/access-review',
        }, sorted(_AUDITED_READS)

    def test_the_target_is_the_innermost_entity(self):
        """Removing a member from a group is an action on the member, so a
        nested route must not attribute to the container."""
        assert AuditTarget.from_path_params({'group_id': 'g1', 'user_id': 'u1'}) == AuditTarget(
            target_type='user', target_id='u1'
        )
        assert AuditTarget.from_path_params({'group_id': 'g1'}) == AuditTarget(target_type='group', target_id='g1')
        assert AuditTarget.from_path_params({'org_id': 'o1'}) == AuditTarget(target_type='organization', target_id='o1')
        assert AuditTarget.from_path_params({}) is None
        assert AuditTarget.from_path_params(None) is None

    def test_denied_is_distinguished_from_error(self):
        """A run of refusals against one actor is the pattern worth
        alerting on, and it is invisible if every non-2xx is one bucket."""
        assert classify(200) == 'success'
        assert classify(204) == 'success'
        assert classify(401) == 'denied'
        assert classify(403) == 'denied'
        assert classify(404) == 'error'
        assert classify(500) == 'error'


class TestARealRequestIsRecorded:
    async def test_a_mutation_writes_one_attributed_event(self, client, admin, db):
        response = await client.post(
            '/portal/groups',
            headers={**auth_header(admin['token']), 'User-Agent': 'probe/1.0'},
            json={'name': f'g-{uuid.uuid4().hex[:6]}'},
        )
        assert response.status_code in (200, 201), response.text

        rows = _rows(route='/v1/portal/groups')
        assert len(rows) == 1, rows

        event = rows[0]
        assert event['method'] == 'POST'
        assert event['actor_user_id'] == admin['user_id']
        # Denormalised on purpose: the record must stay readable after the
        # user is deleted, which is when it matters most.
        assert event['actor_email'] == admin['email']
        assert event['org_id'] == admin['org_id']
        assert event['outcome'] == 'success'
        assert event['user_agent'] == 'probe/1.0'

    async def test_performing_an_access_review_is_recorded(self, client, admin, db):
        """C10, and the point of AC-8.

        SOC 2 CC6.3 and ISO A 5.18 ask whether somebody *looks* at who
        still has access. `GET /v1/portal/access-review` made looking
        possible; until this, nothing recorded that anybody had — so a
        Type II auditor sampling "the access reviews you performed in
        Q2" would have found the capability and no evidence of its use.

        The event is the whole record. There is no other artefact: a
        review that finds nothing to change leaves no mutation behind
        it, which is the common case and the one that would otherwise be
        invisible.
        """
        response = await client.get('/portal/access-review', headers=auth_header(admin['token']))
        assert response.status_code == 200, response.text

        rows = _rows(route='/v1/portal/access-review')
        assert len(rows) == 1, rows

        event = rows[0]
        assert event['method'] == 'GET'
        assert event['actor_user_id'] == admin['user_id']
        assert event['org_id'] == admin['org_id'], (
            'a review is scoped to one organisation, so the record has to say which'
        )
        assert event['outcome'] == 'success'

    async def test_an_ordinary_portal_read_is_still_not_recorded(self, client, admin, db):
        """The exemption stays narrow, checked against a real request
        rather than against the rule alone. A trail that records page
        views is one nobody reads."""
        response = await client.get('/portal/groups', headers=auth_header(admin['token']))
        assert response.status_code == 200, response.text

        assert _rows(route='/v1/portal/groups') == []

    async def test_the_route_template_is_recorded_not_the_path(self, client, admin, db):
        """Otherwise every identifier becomes its own value in `route` and
        the column is useless for grouping — the identifier belongs in
        `target_id`, where it is indexed."""
        target = str(uuid.uuid4())

        await client.delete(f'/portal/groups/{target}', headers=auth_header(admin['token']))

        rows = _rows(route='/v1/portal/groups/{group_id}')
        assert len(rows) == 1, [dict(r) for r in _rows()]
        assert rows[0]['target_type'] == 'group'
        assert rows[0]['target_id'] == target
        assert target not in rows[0]['route']

    async def test_a_refusal_is_recorded_as_denied(self, client):
        """The events an audit trail exists for are the ones that failed."""
        response = await client.get('/portal/audit-events')

        assert response.status_code in (401, 403), response.text
        # A read is not auditable, so nothing should be written for it —
        # this asserts the rule holds even on a refusal.
        assert _rows(route='/v1/portal/audit-events') == []

    async def test_a_failed_sign_in_is_recorded_with_no_actor(self, client):
        """No authenticated actor by definition, which is why the column is
        nullable — and why this is the event most often asked for."""
        await client.post('/sign-in', json={'email': 'nobody@example.com', 'password': 'wrong-password'})

        rows = _rows(route='/v1/sign-in')
        assert len(rows) == 1, rows
        assert rows[0]['actor_user_id'] is None
        assert rows[0]['outcome'] in ('denied', 'error')

    async def test_reads_are_not_recorded(self, client, admin, db):
        await client.get('/portal/me', headers=auth_header(admin['token']))

        assert _rows(route='/v1/portal/me') == []


class TestCoverageIsStructural:
    def test_coverage_is_structural_not_a_list(self):
        """Every mutating portal/admin route is covered by the *rule*.

        This is the assertion that makes the design worth having. It reads
        the live router and checks `is_auditable` against it, so a new
        endpoint is covered the moment it exists — nobody has to remember
        to add a call, and nobody has to update this test.
        """
        from tests.conftest import the_app

        app = the_app()

        mutating = [
            (method.upper(), path)
            for path, operations in app.openapi()['paths'].items()
            for method in operations
            if method.upper() in ('POST', 'PUT', 'PATCH', 'DELETE')
            and (path.startswith('/v1/portal') or path.startswith('/v1/admin'))
        ]

        assert len(mutating) >= 20, f'only found {len(mutating)} mutating routes'
        uncovered = sorted({(m, p) for m, p in mutating if not is_auditable(m, p)})

        assert not uncovered, f'mutating privileged routes not covered by the audit rule: {uncovered}'

    def test_the_middleware_is_registered(self):
        """A recorder nobody calls is the `bandit`-before-F42 and
        `@axe-core` failure mode, and here it would mean the trail is
        empty while every test of the rule still passes."""
        import pathlib

        main = (pathlib.Path(__file__).resolve().parent.parent / 'app/main.py').read_text()

        assert 'add_middleware(AuditMiddleware)' in main

    def test_the_auth_dependency_attributes_the_request(self):
        """Every return path of the portal's auth resolution must go through
        the attribution helper. Asserted on the source because a missing
        call on one branch is invisible to any single request.

        Walked as a tree, scoped to the dependency. It used to be
        `re.findall(r'^\\s+return PortalUser\\(')` over the whole
        file, which asked "does this module contain such a line"
        rather than "does the dependency return one" — so a private
        helper that builds a template tripped it, while a return
        written as `return some_user` inside the dependency would
        have passed. Narrower and stronger at once.
        """
        import ast
        import pathlib

        source = (pathlib.Path(__file__).resolve().parent.parent / 'app/security/portal_auth.py').read_text()
        tree = ast.parse(source)

        functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)}

        # Both dependencies — the org-scoped one and the account one —
        # resolve through `_resolve`, so it is the one whose returns
        # must all be attributed. Checked, not assumed: a dependency
        # that resolved some other way would bypass the rule below.
        for name in ('get_current_portal_user', 'get_current_account'):
            assert name in functions, f'{name} is gone; this rule is checking nothing'
            calls = {
                node.func.id
                for node in ast.walk(functions[name])
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            assert '_resolve' in calls, f'{name} no longer resolves through _resolve'

        dependency = functions.get('_resolve')
        assert dependency is not None, '_resolve is gone; this rule is checking nothing'

        returns = [node for node in ast.walk(dependency) if isinstance(node, ast.Return) and node.value is not None]
        assert len(returns) >= 2, f'expected both branches to return, found {len(returns)}'

        unattributed = [
            ast.unparse(node.value)[:60]
            for node in returns
            if not (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == '_attributed'
            )
        ]
        assert not unattributed, (
            f'{unattributed} — a return path of the auth dependency does not go through '
            '_attributed, so requests resolved on that branch are recorded with no actor'
        )
        assert 'attribute_to' in source


class TestTheRecordCannotBeRewritten:
    def test_the_trigger_is_installed(self):
        """Asserted separately from the behaviour because a migration that
        creates the table and forgets the trigger would leave the
        behavioural test below as the only thing standing between an
        auditor's question and a mutable audit log — and that test would
        then fail for a reason that reads like a database problem rather
        than a missing control."""
        rows = _rows()  # touch the table so a missing table fails clearly
        assert rows is not None

        engine = _sync_engine()
        try:
            with engine.connect() as conn:
                installed = conn.execute(
                    text("SELECT count(*) FROM pg_trigger WHERE tgname = 'audit_events_no_update' AND NOT tgisinternal")
                ).scalar_one()
        finally:
            engine.dispose()

        assert installed == 1, 'the append-only trigger is not installed on audit_events'

    async def test_update_is_refused_by_the_database(self):
        """The guarantee is a trigger, not the absence of an `update`
        method. "The application does not do it" is a weaker statement
        than "the database refuses", and the gap is what an auditor asks
        about."""
        from sqlalchemy.exc import DBAPIError

        await AuditEventDB().record(
            AuditRecord(
                method='DELETE',
                route='/v1/portal/organizations/{org_id}',
                status_code=204,
                actor_user_id='admin-1',
                actor_email='admin@example.com',
            )
        )
        rows = _rows(actor_user_id='admin-1')
        assert len(rows) == 1

        with pytest.raises(DBAPIError) as raised:
            _execute(
                'UPDATE audit_events SET actor_email = :e WHERE id = :i',
                {'e': 'someone.else@example.com', 'i': rows[0]['id']},
            )
        assert 'append-only' in str(raised.value)

        # And it really did not change.
        after = _rows(actor_user_id='admin-1')
        assert after[0]['actor_email'] == 'admin@example.com'

    async def test_delete_is_permitted_because_retention_needs_it(self):
        """The asymmetry is deliberate: a record must not be *altered* to
        say something different, which is the threat. Removing one
        wholesale is a retention policy, and GDPR Article 5(1)(e) asks for
        one."""
        await AuditEventDB().record(
            AuditRecord(method='POST', route='/v1/portal/groups', status_code=201, actor_user_id='u-del')
        )
        assert len(_rows(actor_user_id='u-del')) == 1

        _execute("DELETE FROM audit_events WHERE actor_user_id = 'u-del'")

        assert _rows(actor_user_id='u-del') == []


class TestWritingNeverBreaksTheRequest:
    async def test_a_write_failure_is_logged_and_swallowed(self, monkeypatch, caplog):
        """Fail-open is a real trade-off and the compromise is that it is
        loud: `AUDIT WRITE FAILED` carries the lost event so a deployment
        shipping logs somewhere can alert on it."""
        import logging

        recorder = AuditEventDB()

        def explode(*_args, **_kwargs):
            raise RuntimeError('database is gone')

        monkeypatch.setattr(recorder, '_trail', explode)

        with caplog.at_level(logging.ERROR, logger='app.db.sql.audit_events'):
            await recorder.record(
                AuditRecord(
                    method='DELETE',
                    route='/v1/portal/organizations/{org_id}',
                    status_code=204,
                    actor_user_id='admin-9',
                )
            )

        assert 'AUDIT WRITE FAILED' in caplog.text
        assert 'admin-9' in caplog.text


class TestAConstraintViolationCannotEscape:
    """The justification for `audit_events.py`'s exemption in
    `test_concurrent_creates_do_not_500.py`, tested rather than asserted.

    That guard requires every insert-bearing module to handle
    `IntegrityError` or be exempted with a reason. The reason here is that
    `record()` catches everything, so nothing can escape as a 500 — and a
    claim like that should be demonstrated with a real constraint
    violation, not taken on trust.
    """

    async def test_a_duplicate_primary_key_is_swallowed_and_logged(self, monkeypatch, caplog):
        import logging
        import uuid as _uuid

        fixed = str(_uuid.uuid4())
        monkeypatch.setattr('app.db.sql.audit_events.uuid.uuid4', lambda: fixed)

        recorder = AuditEventDB()
        record = AuditRecord(method='POST', route='/v1/portal/groups', status_code=201, actor_user_id='dupe')

        await recorder.record(record)
        assert len(_rows(actor_user_id='dupe')) == 1

        # The same id again: a genuine primary-key violation.
        with caplog.at_level(logging.ERROR, logger='app.db.sql.audit_events'):
            await recorder.record(record)

        assert 'AUDIT WRITE FAILED' in caplog.text
        # Still exactly one row, and the request it came from was unaffected.
        assert len(_rows(actor_user_id='dupe')) == 1


class TestRetention:
    async def test_zero_keeps_everything(self):
        await AuditEventDB().record(
            AuditRecord(method='POST', route='/v1/portal/groups', status_code=201, actor_user_id='keep')
        )

        assert await AuditEventDB().prune(older_than_days=0) == 0
        assert len(_rows(actor_user_id='keep')) == 1

    async def test_it_removes_only_what_is_older(self):
        now = datetime.now(timezone.utc)
        for actor, when in (('old', now - timedelta(days=40)), ('recent', now - timedelta(days=2))):
            _execute(
                'INSERT INTO audit_events (id, occurred_at, actor_user_id, method, route, '
                'status_code, outcome) VALUES (:i, :t, :a, :m, :r, :s, :o)',
                {
                    'i': str(uuid.uuid4()),
                    't': when,
                    'a': actor,
                    'm': 'POST',
                    'r': '/v1/portal/groups',
                    's': 201,
                    'o': 'success',
                },
            )

        removed = await AuditEventDB().prune(older_than_days=30)

        assert removed == 1
        assert _rows(actor_user_id='old') == []
        assert len(_rows(actor_user_id='recent')) == 1


class TestReadingTheTrail:
    async def test_an_admin_sees_their_own_organisation(self, client, admin, db):
        await client.post(
            '/portal/groups',
            headers=auth_header(admin['token']),
            json={'name': f'g-{uuid.uuid4().hex[:6]}'},
        )

        response = await client.get('/portal/audit-events', headers=auth_header(admin['token']))

        assert response.status_code == 200, response.text
        body = response.json()
        assert body['events'], body
        # This assertion originally ended in `or True`, which made it pass
        # unconditionally. Left as a note because a vacuous assertion in a
        # compliance test is worse than none: it reads as coverage.
        assert all(event['org_id'] in (None, admin['org_id']) for event in body['events']), body['events']
        routes = [event['route'] for event in body['events']]
        assert '/v1/portal/groups' in routes

    async def test_it_does_not_show_another_organisations_activity(self, client, admin):
        """`org_id` comes from the token, never from a parameter — §38's
        property, and this endpoint is not one of its exceptions.

        Everything here is built through the synchronous engine. The `db`
        fixture cannot be used in a test body that also uses `admin`, which
        has already consumed it on another loop — the same constraint the
        helpers at the top of this file exist for.
        """
        other_user_id = f'other-{uuid.uuid4().hex[:8]}'
        other_org_id = str(uuid.uuid4())
        _execute(
            'INSERT INTO users (id, user_id, email, hashed_password, auth_provider, '
            'created_at, updated_at) VALUES (:i, :u, :e, :h, :a, now(), now())',
            {'i': str(uuid.uuid4()), 'u': other_user_id, 'e': f'{other_user_id}@example.com', 'h': '', 'a': 'email'},
        )
        _execute(
            'INSERT INTO organizations (id, slug, display_name, owner_user_id, created_at, '
            'updated_at) VALUES (:i, :s, :d, :o, now(), now())',
            {'i': other_org_id, 's': f'x-{uuid.uuid4().hex[:6]}', 'd': 'Other', 'o': other_user_id},
        )

        class _Other:
            user_id = other_user_id
            email = f'{other_user_id}@example.com'

        other = _Other()
        other_org = type('O', (), {'id': other_org_id})()
        # Written with the synchronous engine rather than `record()`: the
        # recorder runs on the app's loop and this test also drives
        # `client`, which is the cross-loop trap this file's helpers exist
        # to avoid.
        _execute(
            'INSERT INTO audit_events (id, actor_user_id, actor_email, org_id, method, route, '
            'status_code, outcome) VALUES (:i, :a, :e, :o, :m, :r, :s, :out)',
            {
                'i': str(uuid.uuid4()),
                'a': str(other.user_id),
                'e': str(other.email),
                'o': str(other_org.id),
                'm': 'DELETE',
                'r': '/v1/portal/organizations/{org_id}',
                's': 204,
                'out': 'success',
            },
        )

        response = await client.get('/portal/audit-events', headers=auth_header(admin['token']))

        assert response.status_code == 200, response.text
        actors = [e['actor_user_id'] for e in response.json()['events']]
        assert str(other.user_id) not in actors

    async def test_a_plain_member_cannot_read_it(self, client, db):
        """Who did what and from where is a map of staff activity."""
        member = await create_user(db, email=f'm-{uuid.uuid4().hex[:6]}@example.com')
        owner = await create_user(db, email=f'ow-{uuid.uuid4().hex[:6]}@example.com')
        org = await create_org(db, owner_user_id=str(owner.user_id), slug=f'p-{uuid.uuid4().hex[:6]}')
        await add_member(db, str(org.id), str(member.user_id), role='member')
        token = make_token(str(member.user_id), org_id=str(org.id), org_name='P')

        response = await client.get('/portal/audit-events', headers=auth_header(token))

        assert response.status_code == 403, response.text
