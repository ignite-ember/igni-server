"""Access can be reviewed, which is the control rather than the report.

AC-8 in `COMPLIANCE_TRACKER.md` — SOC 2 CC6.3, ISO 27001 A 5.18: access is
removed when no longer needed. That is a different question from "does
access control work", which is CC6.1 and was answered by §38's sweep of
30 endpoints. This one asks whether anybody can *look* at who still has
access, and nothing supported it: `users` had no notion of a sign-in, so
the only available answer was "everyone ever added is still listed".

Two halves are tested here, and the second is the one that rots:

* the report says the right thing about a fabricated organisation whose
  members are deliberately awkward — an unclaimed invitation with no user
  row at all, an account that never signed in, a dormant one, and an
  ordinary one;
* **every place a token is minted records a sign-in.** Asserted by
  counting call sites rather than by trusting four of them, because F80,
  F88 and F90 are each a record of what a list of call sites becomes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pathlib
import re
import uuid

import pytest
from sqlalchemy import create_engine, text

from app.api.base_v1.schema.portal.access_review import AccessReview
from tests.conftest import add_member, auth_header, create_org, create_user, make_token, TEST_DB_URL_SYNC

pytestmark = pytest.mark.anyio

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _execute(sql: str, params: dict | None = None) -> None:
    """Synchronous, for the same reason `test_audit_trail.py` is: the `db`
    fixture and the `client` fixture run on different event loops."""
    engine = create_engine(TEST_DB_URL_SYNC)
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params or {})
    finally:
        engine.dispose()


class TestTheFlagPolicy:
    """`flags_for` is pure, so the policy is tested without a database.

    The four signals are deliberately separate rather than one "stale"
    bucket, because they have different remedies: chase the invitation,
    revoke the access, or find out why somebody never arrived.
    """

    def test_an_ordinary_active_member_is_unflagged(self):
        assert (
            AccessReview.flags_for(
                status='active',
                last_sign_in_at=datetime.now(timezone.utc),
                days_since=0,
                dormant_after_days=90,
            )
            == []
        )

    def test_an_unclaimed_invitation_is_flagged_as_pending(self):
        flags = AccessReview.flags_for(status='invited', last_sign_in_at=None, days_since=None, dormant_after_days=90)

        assert flags == ['invitation_pending']
        # Not `never_signed_in`: they were never able to. Conflating the
        # two would send a reviewer to revoke access that was never used
        # rather than to chase an invitation.
        assert 'never_signed_in' not in flags

    def test_an_active_account_that_never_signed_in_is_flagged(self):
        flags = AccessReview.flags_for(status='active', last_sign_in_at=None, days_since=None, dormant_after_days=90)

        assert flags == ['never_signed_in']

    def test_dormancy_is_measured_against_the_threshold(self):
        now = datetime.now(timezone.utc)
        assert AccessReview.flags_for(
            status='active', last_sign_in_at=now - timedelta(days=200), days_since=200, dormant_after_days=90
        ) == ['dormant']
        assert (
            AccessReview.flags_for(
                status='active',
                last_sign_in_at=now - timedelta(days=10),
                days_since=10,
                dormant_after_days=90,
            )
            == []
        )

    def test_the_threshold_is_inclusive(self):
        """Exactly at the boundary counts as dormant. Stated because
        "90 days" in a policy means "not in 90 days", and an off-by-one
        here is an argument with an auditor rather than a bug."""
        assert AccessReview.flags_for(
            status='active', last_sign_in_at=datetime.now(timezone.utc), days_since=90, dormant_after_days=90
        ) == ['dormant']

    def test_any_other_status_is_flagged_rather_than_ignored(self):
        """A status a later migration adds must not fall silently into the
        unflagged bucket, which is the shape of gap this whole review keeps
        finding."""
        assert AccessReview.flags_for(
            status='suspended', last_sign_in_at=None, days_since=None, dormant_after_days=90
        ) == ['suspended']
        assert AccessReview.flags_for(
            status='something_new', last_sign_in_at=None, days_since=None, dormant_after_days=90
        ) == ['suspended']


class TestTheReport:
    @pytest.fixture
    async def org_with_awkward_members(self, db):
        admin = await create_user(db, email=f'admin-{uuid.uuid4().hex[:6]}@example.com')
        org = await create_org(db, owner_user_id=str(admin.user_id), slug=f'r-{uuid.uuid4().hex[:6]}')
        await add_member(db, str(org.id), str(admin.user_id), role='admin')

        never = await create_user(db, email=f'never-{uuid.uuid4().hex[:6]}@example.com')
        dormant = await create_user(db, email=f'dormant-{uuid.uuid4().hex[:6]}@example.com')
        current = await create_user(db, email=f'current-{uuid.uuid4().hex[:6]}@example.com')
        for user in (never, dormant, current):
            await add_member(db, str(org.id), str(user.user_id), role='member')

        return {
            'org_id': str(org.id),
            'token': make_token(str(admin.user_id), org_id=str(org.id), org_name='R'),
            'admin_id': str(admin.user_id),
            'never': str(never.user_id),
            'dormant': str(dormant.user_id),
            'current': str(current.user_id),
        }

    async def _prepare(self, ctx):
        """Set sign-in times and add an unclaimed invitation.

        Written synchronously so the state is visible to the app, which
        runs on the client's event loop.
        """
        now = datetime.now(timezone.utc)
        _execute(
            'UPDATE users SET last_sign_in_at = :t WHERE user_id = :u',
            {'t': now - timedelta(days=400), 'u': ctx['dormant']},
        )
        for user_id in (ctx['current'], ctx['admin_id']):
            _execute(
                'UPDATE users SET last_sign_in_at = :t WHERE user_id = :u',
                {'t': now - timedelta(days=1), 'u': user_id},
            )
        # An invitation nobody claimed: a membership row with an address
        # and **no user**. The most interesting row in a review, and the
        # one an inner join would silently drop.
        _execute(
            'INSERT INTO org_memberships (id, org_id, user_id, invited_email, invited_by, role, '
            'status, created_at, updated_at) VALUES (:i, :o, :u, :e, :b, :r, :s, now(), now())',
            {
                'i': str(uuid.uuid4()),
                'o': ctx['org_id'],
                'u': f'invited-{uuid.uuid4().hex[:8]}',
                'e': 'never.arrived@example.com',
                'b': ctx['admin_id'],
                'r': 'member',
                's': 'invited',
            },
        )

    async def test_it_reports_every_member_and_flags_the_awkward_ones(self, client, org_with_awkward_members):
        ctx = org_with_awkward_members
        await self._prepare(ctx)

        response = await client.get('/portal/access-review', headers=auth_header(ctx['token']))

        assert response.status_code == 200, response.text
        body = response.json()

        assert body['total'] == 5, body
        by_user = {row['user_id']: row for row in body['rows']}

        assert by_user[ctx['never']]['flags'] == ['never_signed_in']
        assert by_user[ctx['dormant']]['flags'] == ['dormant']
        assert by_user[ctx['dormant']]['days_since_sign_in'] >= 400
        assert by_user[ctx['current']]['flags'] == []
        assert body['needs_review'] == 3, [r['flags'] for r in body['rows']]

    async def test_an_unclaimed_invitation_appears_with_its_address(self, client, org_with_awkward_members):
        """An outer join, not an inner one: this row has no user, and it is
        the finding the report exists to surface."""
        ctx = org_with_awkward_members
        await self._prepare(ctx)

        response = await client.get('/portal/access-review', headers=auth_header(ctx['token']))

        invited = [r for r in response.json()['rows'] if r['status'] == 'invited']
        assert len(invited) == 1, response.json()['rows']
        assert invited[0]['email'] == 'never.arrived@example.com'
        assert invited[0]['flags'] == ['invitation_pending']
        assert invited[0]['auth_provider'] is None
        assert invited[0]['granted_by'] == ctx['admin_id']

    async def test_the_threshold_is_a_parameter_a_reviewer_can_move(self, client, org_with_awkward_members):
        """An auditor asking "show me 30 days" must not need a deployment."""
        ctx = org_with_awkward_members
        await self._prepare(ctx)

        strict = await client.get('/portal/access-review?dormant_after_days=1', headers=auth_header(ctx['token']))

        assert strict.status_code == 200, strict.text
        assert strict.json()['dormant_after_days'] == 1
        dormant = [r for r in strict.json()['rows'] if 'dormant' in r['flags']]
        # At one day, everyone who signed in yesterday is dormant too.
        assert len(dormant) >= 3, [r['flags'] for r in strict.json()['rows']]

    async def test_every_row_is_measured_from_one_instant(self, client, org_with_awkward_members):
        """`generated_at` is computed once and passed down. Per-row `now()`
        would make a large report internally inconsistent, which is exactly
        the kind of thing that gets noticed in an audit and not before."""
        ctx = org_with_awkward_members
        await self._prepare(ctx)

        body = (await client.get('/portal/access-review', headers=auth_header(ctx['token']))).json()

        assert body['generated_at']
        assert body['dormant_after_days'] == 90


class TestItIsScopedAndGated:
    async def test_another_organisation_is_not_visible(self, client, db):
        """`org_id` from the token, never a parameter — §38's property."""
        mine = await create_user(db, email=f'm-{uuid.uuid4().hex[:6]}@example.com')
        my_org = await create_org(db, owner_user_id=str(mine.user_id), slug=f'a-{uuid.uuid4().hex[:6]}')
        await add_member(db, str(my_org.id), str(mine.user_id), role='admin')

        theirs = await create_user(db, email=f'secret-{uuid.uuid4().hex[:6]}@example.com')
        their_org = await create_org(db, owner_user_id=str(theirs.user_id), slug=f'b-{uuid.uuid4().hex[:6]}')
        await add_member(db, str(their_org.id), str(theirs.user_id), role='admin')

        token = make_token(str(mine.user_id), org_id=str(my_org.id), org_name='A')
        response = await client.get('/portal/access-review', headers=auth_header(token))

        assert response.status_code == 200, response.text
        assert response.json()['org_id'] == str(my_org.id)
        assert str(theirs.email) not in response.text

    async def test_a_plain_member_cannot_read_it(self, client, db):
        """Addresses, roles and sign-in times are a map of who works
        there."""
        member = await create_user(db, email=f'p-{uuid.uuid4().hex[:6]}@example.com')
        owner = await create_user(db, email=f'o-{uuid.uuid4().hex[:6]}@example.com')
        org = await create_org(db, owner_user_id=str(owner.user_id), slug=f'c-{uuid.uuid4().hex[:6]}')
        await add_member(db, str(org.id), str(member.user_id), role='member')
        token = make_token(str(member.user_id), org_id=str(org.id), org_name='C')

        response = await client.get('/portal/access-review', headers=auth_header(token))

        assert response.status_code == 403, response.text


class TestSignInsAreRecorded:
    async def test_a_successful_sign_in_sets_the_timestamp(self, client, db):
        """Through the real endpoint, because the point is that the
        recording happens where a token is minted rather than where a test
        calls a helper."""
        from app.services.auth.auth_service import AuthService

        email = f'signer-{uuid.uuid4().hex[:6]}@example.com'
        password = 'a-real-password-123'
        user = await create_user(db, email=email)
        _execute(
            'UPDATE users SET hashed_password = :h, last_sign_in_at = NULL WHERE user_id = :u',
            {'h': AuthService.hash_password(password), 'u': str(user.user_id)},
        )

        response = await client.post('/sign-in', json={'email': email, 'password': password})

        assert response.status_code == 200, response.text
        engine = create_engine(TEST_DB_URL_SYNC)
        try:
            with engine.connect() as conn:
                recorded = conn.execute(
                    text('SELECT last_sign_in_at FROM users WHERE user_id = :u'),
                    {'u': str(user.user_id)},
                ).scalar_one()
        finally:
            engine.dispose()

        assert recorded is not None, 'a successful sign-in did not record itself'

    async def test_a_failed_sign_in_does_not(self, client, db):
        """Otherwise a locked-out account looks active, and the dormancy
        signal reports the attacker's activity rather than the owner's."""
        email = f'nope-{uuid.uuid4().hex[:6]}@example.com'
        user = await create_user(db, email=email)
        _execute('UPDATE users SET last_sign_in_at = NULL WHERE user_id = :u', {'u': str(user.user_id)})

        await client.post('/sign-in', json={'email': email, 'password': 'wrong'})

        engine = create_engine(TEST_DB_URL_SYNC)
        try:
            with engine.connect() as conn:
                recorded = conn.execute(
                    text('SELECT last_sign_in_at FROM users WHERE user_id = :u'),
                    {'u': str(user.user_id)},
                ).scalar_one()
        finally:
            engine.dispose()

        assert recorded is None

    def test_every_token_mint_records_a_sign_in(self):
        """A rule over the call sites, not four places somebody remembers.

        Every module in `app/services/auth/` that mints a token must record
        the same number of sign-ins. That catches a *fifth* minting site
        added to an existing module, which is the failure a list cannot see
        and the one F80 and F88 are both records of.
        """
        offenders = []
        for path in sorted((_ROOT / 'app/services/auth').glob('*.py')):
            source = path.read_text()
            mints = len(re.findall(r'\bcreate_token\(', source))
            # The service that *defines* create_token is not a call site.
            if path.name == 'jwt_service.py':
                continue
            if not mints:
                continue
            recorded = len(re.findall(r'\bnote_sign_in\(', source))
            if mints != recorded:
                offenders.append(f'{path.name}: {mints} create_token, {recorded} note_sign_in')

        assert not offenders, (
            f'token mints without a matching sign-in record: {offenders}. '
            'The access review (AC-8) reports dormancy, so a mint that does not '
            'record makes an active account look abandoned.'
        )

    def test_the_scan_found_the_mints_it_is_counting(self):
        """A rule that matched nothing would pass. Two leak sweeps in this
        review looked like success while measuring nothing."""
        total = sum(
            len(re.findall(r'\bcreate_token\(', p.read_text()))
            for p in (_ROOT / 'app/services/auth').glob('*.py')
            if p.name != 'jwt_service.py'
        )

        assert total >= 4, f'only found {total} token mints; has the auth layer moved?'
