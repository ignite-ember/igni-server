"""Personal data can be bounded and erased.

DP-6 and DP-7 in `COMPLIANCE_TRACKER.md` — GDPR Article 5(1)(e) storage
limitation and Article 17 right to erasure.

**Retention** applies to the audit trail, which grows for the life of a
deployment: the deployment's own trail in the shared plane, and every
organisation's trail in its own database.

**Erasure** reaches the services that hold a person's work — today the
documents service — and redacts rather than deletes, because a page
carries other people's comments too.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pathlib
import uuid

import pytest
from sqlalchemy import create_engine, text

from app.config import settings
from app.db.sql.audit_events import AuditEventDB
from app.db.sql.models.audit_event import AuditEvent
from app.services.identity.service_subjects import ServiceSubjects
from app.services.retention import Retention
from tests.conftest import add_member, auth_header, create_org, create_user, make_token, put_in_org, TEST_DB_URL_SYNC

_LOCK = 0x1911_2_5445


def _execute(sql: str, params: dict | None = None):
    engine = create_engine(TEST_DB_URL_SYNC)
    try:
        with engine.begin() as conn:
            result = conn.execute(text(sql), params or {})
            return result.fetchall() if result.returns_rows else []
    finally:
        engine.dispose()


def _deployment_event(*, occurred_at: datetime, route: str = '/v1/auth/sign-in') -> None:
    """A row in the deployment's own trail, in the shared plane."""
    _execute(
        'INSERT INTO audit_events (id, occurred_at, method, route, status_code, outcome) '
        "VALUES (:i, :t, 'POST', :r, 200, 'success')",
        {'i': str(uuid.uuid4()), 't': occurred_at, 'r': route},
    )


def _org_event(*, occurred_at: datetime, org_id: str) -> AuditEvent:
    return AuditEvent(
        id=str(uuid.uuid4()),
        occurred_at=occurred_at,
        org_id=org_id,
        method='POST',
        route='/v1/portal/groups',
        status_code=201,
        outcome='success',
    )


class TestTheEndpoint:
    @pytest.fixture
    async def admin(self, db):
        person = await create_user(db, email=f'admin-{uuid.uuid4().hex[:6]}@example.com')
        org = await create_org(db, owner_user_id=str(person.user_id), slug=f'e-{uuid.uuid4().hex[:6]}')
        await add_member(db, str(org.id), str(person.user_id), role='admin')
        return {'token': make_token(str(person.user_id), org_id=str(org.id), org_name='E'), 'org_id': str(org.id)}

    @pytest.fixture
    def documents(self, monkeypatch):
        """The registered services, recording who they were told to redact.

        How the fan-out reaches each one is
        `test_gdpr_requests_reach_every_service.py`'s subject; this is
        the endpoint's.
        """
        told: list[str] = []

        async def _erase(self, user_id: str, *, org_id: str) -> list[str]:
            told.append(user_id)
            return ['Artifacts']

        monkeypatch.setattr(ServiceSubjects, 'erase', _erase)
        return told

    async def test_a_person_with_an_account_is_redacted_in_every_service(self, client, db, admin, documents):
        subject = await create_user(db, email='jane@example.com')

        response = await client.post(
            '/portal/erasure', headers=auth_header(admin['token']), json={'email': 'jane@example.com'}
        )

        assert response.status_code == 200, response.text
        assert response.json()['services_told'] == ['Artifacts']
        assert documents == [str(subject.user_id)]

    async def test_the_response_is_not_a_disclosure(self, client, admin, documents):
        """Masked: an endpoint that erases an address must not echo it back
        into a response somebody might log."""
        response = await client.post(
            '/portal/erasure', headers=auth_header(admin['token']), json={'email': 'jane@example.com'}
        )

        assert response.json()['email'] == 'j***@example.com'
        assert 'jane@example.com' not in response.text

    async def test_an_address_with_no_account_tells_nobody(self, client, admin, documents):
        response = await client.post(
            '/portal/erasure', headers=auth_header(admin['token']), json={'email': 'nobody@example.com'}
        )

        assert response.status_code == 200, response.text
        assert response.json()['services_told'] == []
        assert documents == []

    async def test_it_is_recorded_in_the_audit_trail_without_the_address(self, client, org_db, admin, documents):
        """F90 audits this automatically because it is a `POST` under
        `/v1/portal`. The audit trail is retained, so it must not become
        the place the erased address survives. An admin's action is in
        their organisation's own trail."""
        await client.post('/portal/erasure', headers=auth_header(admin['token']), json={'email': 'jane@example.com'})

        async with org_db(admin['org_id']) as session:
            rows = (
                await session.execute(
                    text("SELECT detail::text AS d FROM audit_events WHERE route = '/v1/portal/erasure'")
                )
            ).all()
        assert len(rows) == 1, rows
        assert 'jane@example.com' not in (rows[0].d or '')
        assert 'j***@example.com' in (rows[0].d or '')

    async def test_a_plain_member_cannot_erase(self, client, db):
        member = await create_user(db, email=f'p-{uuid.uuid4().hex[:6]}@example.com')
        owner = await create_user(db, email=f'o-{uuid.uuid4().hex[:6]}@example.com')
        org = await create_org(db, owner_user_id=str(owner.user_id), slug=f'f-{uuid.uuid4().hex[:6]}')
        await add_member(db, str(org.id), str(member.user_id), role='member')
        token = make_token(str(member.user_id), org_id=str(org.id), org_name='F')

        response = await client.post('/portal/erasure', headers=auth_header(token), json={'email': 'jane@example.com'})

        assert response.status_code == 403, response.text


class TestTheRetentionRunner:
    """A setting is not a limit. SOC 2 asks whether a control *operates*.

    `app/services/retention.py` is what applies the window. Its two
    interesting properties are that it does nothing unless configured —
    so a deployment that has not made a retention decision gets no
    background task and no surprise deletions — and that only one replica
    prunes per round.
    """

    async def test_it_does_nothing_when_no_window_is_set(self, monkeypatch):
        monkeypatch.setattr(settings, 'audit_retention_days', 0)
        monkeypatch.setattr(settings, 'service_event_retention_days', 0)
        monkeypatch.setattr(settings, 'producer_feed_retention_days', 0)
        _deployment_event(occurred_at=datetime.now(timezone.utc) - timedelta(days=900))

        assert Retention.is_configured() is False
        nothing = {
            'audit_events': 0,
            'producer_events': 0,
            'producer_feed': 0,
            'producer_exports': 0,
            'revocations': 0,
        }
        assert (await Retention().run_once()).model_dump() == nothing
        assert len(_execute('SELECT 1 AS x FROM audit_events')) == 1

    async def test_it_applies_the_window_to_the_deployments_trail(self, monkeypatch):
        monkeypatch.setattr(settings, 'audit_retention_days', 30)
        now = datetime.now(timezone.utc)
        _deployment_event(occurred_at=now - timedelta(days=400), route='/old')
        _deployment_event(occurred_at=now - timedelta(days=1), route='/new')

        removed = await Retention().run_once()

        assert removed.audit_events == 1
        assert [r.route for r in _execute('SELECT route FROM audit_events')] == ['/new']

    async def test_and_to_every_organisations_trail(self, monkeypatch, db, org_db):
        """Each organisation's trail is in its own database; a window that
        only reached the shared plane would be a limit on half the data."""
        monkeypatch.setattr(settings, 'audit_retention_days', 30)
        owner = await create_user(db, email='owner@example.com')
        org = await create_org(db, owner_user_id=str(owner.user_id), slug='r-org')
        now = datetime.now(timezone.utc)
        await put_in_org(
            str(org.id),
            _org_event(occurred_at=now - timedelta(days=400), org_id=str(org.id)),
            _org_event(occurred_at=now - timedelta(days=1), org_id=str(org.id)),
        )

        removed = await Retention().run_once()

        assert removed.audit_events == 1
        async with org_db(str(org.id)) as session:
            left = (await session.execute(text('SELECT count(*) FROM audit_events'))).scalar_one()
        assert left == 1

    async def test_a_second_instance_skips_the_round(self, monkeypatch):
        """The loop runs in every replica, so it takes an advisory lock.

        Simulated by holding the same lock on another connection, which is
        what a second replica looks like from the database's side.
        """
        monkeypatch.setattr(settings, 'audit_retention_days', 30)
        _deployment_event(occurred_at=datetime.now(timezone.utc) - timedelta(days=400))

        engine = create_engine(TEST_DB_URL_SYNC)
        held = engine.connect()
        try:
            got = held.execute(text('SELECT pg_try_advisory_lock(:k)'), {'k': _LOCK}).scalar()
            assert got, 'could not take the lock to simulate another replica'

            removed = await Retention().run_once()

            assert removed.model_dump() == {
                'audit_events': 0,
                'producer_events': 0,
                'producer_feed': 0,
                'producer_exports': 0,
                'revocations': 0,
            }
            # And the row is still there, which is the point: the other
            # instance will prune it.
            assert len(_execute('SELECT 1 AS x FROM audit_events')) == 1
        finally:
            held.close()
            engine.dispose()

    async def test_the_lock_is_released_even_when_pruning_fails(self, monkeypatch):
        """Holding it would silently stop every other instance from ever
        pruning — the failure mode hardest to notice."""
        monkeypatch.setattr(settings, 'audit_retention_days', 30)

        async def explode(**_kwargs):
            raise RuntimeError('prune failed')

        monkeypatch.setattr(AuditEventDB(), 'prune', explode)

        with pytest.raises(RuntimeError):
            await Retention().run_once()

        # Freely takeable again.
        engine = create_engine(TEST_DB_URL_SYNC)
        try:
            with engine.connect() as conn:
                got = conn.execute(text('SELECT pg_try_advisory_lock(:k)'), {'k': _LOCK}).scalar()
                assert got, 'the advisory lock was not released after a failed round'
                conn.execute(text('SELECT pg_advisory_unlock(:k)'), {'k': _LOCK})
        finally:
            engine.dispose()

    def test_the_lifespan_starts_and_stops_it(self):
        """A runner nobody starts is the `bandit`-before-F42 shape, and
        here it would mean the retention window silently never applies."""
        main = (pathlib.Path(__file__).resolve().parent.parent / 'app/main.py').read_text()

        assert 'Retention()' in main, 'the retention loop is never built'
        assert 'retention.start()' in main, 'the retention loop is never started'
        assert 'await retention.stop()' in main, (
            'the loop is never cancelled on shutdown, which leaks the task and can leave the advisory lock held'
        )
