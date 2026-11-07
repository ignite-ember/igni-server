"""Withdrawing somebody's sessions has to end them, everywhere.

B5, building DEC-10. `AC-11` in the compliance tracker read *"a signed
30-day token cannot be withdrawn while the account exists, so an operator
who suspects a leaked token has no lever short of deleting the person."*
B1 established that half of that was wrong — deactivation cuts
organisation access on the next request — and that half of it was right:
`test_what_deactivation_actually_revokes.py` proves a deactivated
member's credential still authenticates them outside the org, because
deactivation is an authorisation change on a membership and not a
revocation of the credential.

This is the missing lever. `users.tokens_valid_after` is a watermark;
`require_existing_user` refuses any token whose `iat` is at or before it.
One column, no session table, nothing in Redis, and no per-token state to
sweep — which is why it can be checked at F122's single choke point and
therefore on every authenticated route at once.

## Why this is a sweep too

For F122's reason, unchanged: two call sites turned a token into a user id
and both were wrong, so the check is generated from the **live router**
rather than from a list, and a new route is covered the day it is added.

It reuses F122's route generation and both of its maps by import. That is
deliberate. Two sweeps over "every authenticated route" that each decide
for themselves what that means would drift apart, and the one that drifted
would keep passing — which is F115's staleness in a new costume. One
definition, two questions asked of it.

## The one-second window

JWT `iat` is integer seconds (RFC 7519 §4.1.6), so a token minted at
12:00:00.9 carries `iat = 12:00:00`. A watermark taken as a plain
`now()` during that same second — 12:00:00.4 — leaves
`iat <= tokens_valid_after` comparing 12:00:00 against 12:00:00.4, and
the token *survives the revocation intended to kill it*. That is a
one-second hole at exactly the moment an operator is racing an attacker,
which is the only moment this feature is used. `withdraw_sessions`
rounds the watermark up to the next whole second, and the comparison is
`<=`; `TestTheOneSecondWindow` pins each separately, because a revert of
either one alone must fail something.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import create_engine, text

from app.services.auth.jwt_service import JWTService
from tests.conftest import add_member, audit_rows, auth_header, create_org, create_user, make_token, TEST_DB_URL_SYNC

# One definition of "every authenticated route", shared with F122's sweep.
from tests.test_a_deleted_user_cannot_come_back import (
    _DELIBERATELY_NOT_REFUSED,
    _fill,
    _user_authenticated_routes,
    _VALID_REQUESTS,
)

pytestmark = pytest.mark.anyio

_ABSOLUTE = 'http://test'


def _sync_engine():
    """Synchronous, for the reason `test_audit_trail.py` documents: the
    `db` fixture and the `client` fixture run on different event loops, so
    a row the application wrote cannot be read back through `db`."""
    return create_engine(TEST_DB_URL_SYNC)


def _watermark_of(user_id: str) -> datetime | None:
    engine = _sync_engine()
    try:
        with engine.connect() as conn:
            return conn.execute(
                text('SELECT tokens_valid_after FROM users WHERE user_id = :u'), {'u': user_id}
            ).scalar()
    finally:
        engine.dispose()


def _set_watermark(user_id: str, when: datetime) -> None:
    """Put the watermark somewhere the wall clock is not.

    Used only where the alternative is `time.sleep` in a test suite —
    proving that a token minted *after* a revocation works would otherwise
    mean waiting out the rounding. The comparison under test is the same
    one either way; what this avoids is a two-second sleep, not a check.
    """
    engine = _sync_engine()
    try:
        with engine.begin() as conn:
            conn.execute(
                text('UPDATE users SET tokens_valid_after = :w WHERE user_id = :u'),
                {'w': when, 'u': user_id},
            )
    finally:
        engine.dispose()


def _audit_rows(route: str):
    return audit_rows(route=route)


@pytest.fixture
async def team(client, db):
    """An org, an admin, an ordinary member, and a token for each."""
    suffix = uuid.uuid4().hex[:8]
    admin_id, member_id = f'admin-{suffix}', f'member-{suffix}'
    await create_user(db, user_id=admin_id, email=f'{admin_id}@example.invalid', full_name='Admin')
    await create_user(db, user_id=member_id, email=f'{member_id}@example.invalid', full_name='Member')
    org = await create_org(db, owner_user_id=admin_id, slug=f'acme-{suffix}', display_name='Acme')
    await add_member(db, org_id=str(org.id), user_id=member_id, role='member')

    org_id = str(org.id)
    return {
        'org_id': org_id,
        'admin_id': admin_id,
        'member_id': member_id,
        # No `org` claim, which is what a portal sign-in actually mints.
        'admin_token': make_token(admin_id, org_id=org_id),
        'member_token': make_token(member_id),
    }


@pytest.fixture
async def outsider(client, db):
    """Somebody in an organisation of their own, for the cross-org check.

    Set up in a fixture and not in the test body, which is not a style
    choice: the same two `create_user`/`create_org` calls raise asyncpg's
    "attached to a different loop" when made after `client` has served a
    request, and succeed during fixture setup. The neighbouring
    constraint `test_audit_trail.py` documents, met from the other side.
    """
    outsider_id = f'outsider-{uuid.uuid4().hex[:8]}'
    await create_user(db, user_id=outsider_id, email=f'{outsider_id}@example.invalid', full_name='Stranger')
    org = await create_org(db, owner_user_id=outsider_id, slug=f'other-{outsider_id}', display_name='Other')
    return {'user_id': outsider_id, 'org_id': str(org.id)}


class TestTheControl:
    """A refusal only means something if acceptance was on offer first."""

    async def test_a_member_token_works_before_any_revocation(self, client, team):
        me = await client.get('/portal/me', headers=auth_header(team['member_token']))
        assert me.status_code == 200, me.text

    async def test_a_fresh_user_has_no_watermark(self, client, team):
        """`NULL`, not epoch. The revocation check has to distinguish
        "never revoked" from "revoked at the start of time", and a default
        of `now()` on the column would refuse every token ever minted."""
        assert _watermark_of(team['member_id']) is None


class TestTheSweep:
    async def test_a_withdrawn_token_is_refused_on_every_authenticated_route(self, client, team):
        """The whole surface, from the live router.

        The token here belongs to a user who **still exists** and is
        still an active member of the org — the only thing wrong with it
        is that it predates the watermark. That is what separates this
        sweep from F122's: there, refusing was easy because the user was
        gone.
        """
        token = team['member_token']
        revoke = await client.post('/portal/me/revoke-sessions', headers=auth_header(token))
        assert revoke.status_code == 200, revoke.text

        headers = auth_header(token)
        routes = _user_authenticated_routes()
        assert len(routes) > 30, f'only {len(routes)} routes found — has the router changed shape?'

        allowed: list[str] = []
        unreached: list[str] = []
        for method, path in routes:
            if (method, path) in _DELIBERATELY_NOT_REFUSED:
                continue
            request = _VALID_REQUESTS.get((method, path), {})
            response = await client.request(
                method,
                _ABSOLUTE + _fill(path),
                headers=headers,
                json=request.get('json', {}),
                params=request.get('params'),
            )
            if response.status_code == 401:
                continue
            if response.status_code == 422:
                # Body judged before caller — not a refusal, so not a
                # pass either. Reported rather than swallowed.
                unreached.append(f'{method} {path}')
                continue
            allowed.append(f'{method} {path} -> {response.status_code}')

        assert not unreached, (
            'these answered 422 before authorising, so this sweep never asked its question '
            f'about them: {unreached}. Add whatever makes the request valid to '
            '`_VALID_REQUESTS` in test_a_deleted_user_cannot_come_back.py.'
        )
        assert not allowed, (
            'these accepted a token minted before the holder withdrew their sessions:\n  '
            + '\n  '.join(allowed)
            + '\n\nEvery route that trusts a user token must resolve it through '
            '`app.security.subject.require_existing_user`, which is where the '
            '`tokens_valid_after` watermark is compared (DEC-10).'
        )


class TestWithdrawingYourOwn:
    async def test_it_signs_the_caller_out_too(self, client, team):
        """Deliberate, and the point. Exempting the current session would
        leave the credential in the caller's hand working after they asked
        for it to stop."""
        token = team['member_token']
        assert (await client.post('/portal/me/revoke-sessions', headers=auth_header(token))).status_code == 200

        again = await client.get('/portal/me', headers=auth_header(token))
        assert again.status_code == 401, f"the caller's own token survived. Got {again.status_code}"

    async def test_it_reports_the_watermark_the_database_holds(self, client, team):
        """Not a second reading of the clock. Support answering "why am I
        signed out on my laptop" reads this field, and a response that
        disagreed with the column by a millisecond would send them
        looking in the wrong place."""
        response = await client.post('/portal/me/revoke-sessions', headers=auth_header(team['member_token']))
        assert response.status_code == 200, response.text

        reported = datetime.fromisoformat(response.json()['tokens_valid_after'])
        assert response.json()['user_id'] == team['member_id']
        assert reported == _watermark_of(team['member_id'])

    async def test_it_needs_a_token_of_its_own(self, client):
        """An unauthenticated caller must not be able to sign anybody out.
        A revocation endpoint open to strangers is a denial of service
        against every user of the deployment."""
        anonymous = await client.post('/portal/me/revoke-sessions')
        assert anonymous.status_code in (401, 403), anonymous.text

    async def test_signing_in_again_works(self, client, team):
        """One column is enough *because* of this: a token minted after
        the watermark passes, so there is nothing to clean up and no
        un-revoke step. Without it the feature would be a one-way door.

        The watermark is placed in the past rather than sleeping out the
        rounding; the comparison exercised is the same one.
        """
        member_id = team['member_id']
        assert (
            await client.post('/portal/me/revoke-sessions', headers=auth_header(team['member_token']))
        ).status_code == 200
        _set_watermark(member_id, datetime.now(timezone.utc) - timedelta(minutes=5))

        fresh = await client.get('/portal/me', headers=auth_header(make_token(member_id)))
        assert fresh.status_code == 200, f'a token minted after the revocation was refused: {fresh.text}'


class TestAnAdminWithdrawingSomebodyElses:
    async def test_an_admin_can_sign_a_member_out(self, client, team):
        """The lever AC-11 said did not exist."""
        response = await client.post(
            f'/portal/team/{team["member_id"]}/revoke-sessions',
            headers=auth_header(team['admin_token']),
        )
        assert response.status_code == 200, response.text

        member = await client.get('/portal/me', headers=auth_header(team['member_token']))
        assert member.status_code == 401, f'the member kept their session. Got {member.status_code}'

    async def test_it_does_not_sign_the_admin_out(self, client, team):
        """Scoped to the target. An incident-response step that logs the
        responder out mid-incident would not be used."""
        await client.post(
            f'/portal/team/{team["member_id"]}/revoke-sessions',
            headers=auth_header(team['admin_token']),
        )
        admin = await client.get('/portal/me', headers=auth_header(team['admin_token']))
        assert admin.status_code == 200, admin.text

    async def test_an_ordinary_member_cannot_use_it(self, client, team):
        """Admin-only, and checked with a member of the same org so the
        refusal is about the role rather than about membership."""
        response = await client.post(
            f'/portal/team/{team["admin_id"]}/revoke-sessions',
            headers={**auth_header(team['member_token']), 'X-Org-Id': team['org_id']},
        )
        assert response.status_code == 403, response.text
        assert _watermark_of(team['admin_id']) is None, 'a member revoked the admin'

    async def test_an_admin_cannot_reach_outside_their_org(self, client, team, outsider):
        """`require_admin` establishes that the caller administers *some*
        organisation. Without the membership check inside the service,
        that would be enough to sign out any user of the deployment by
        id — a denial of service granted by the endpoint built to stop
        one. This is the test that would catch the check being dropped.
        """
        assert outsider['org_id'] != team['org_id']
        stranger = outsider['user_id']

        response = await client.post(
            f'/portal/team/{stranger}/revoke-sessions',
            headers=auth_header(team['admin_token']),
        )
        assert response.status_code == 404, response.text
        assert _watermark_of(stranger) is None, "an admin of another org withdrew a stranger's sessions"

        still_working = await client.get('/portal/me', headers=auth_header(make_token(stranger)))
        assert still_working.status_code == 200, still_working.text


class TestItIsInTheAuditTrail:
    async def test_the_admin_action_is_recorded_with_actor_and_target(self, client, team):
        """Nothing in the handler does this — the middleware records every
        mutation under `/v1/portal` structurally (F114). Which means this
        test is checking that the *rule* covers the new route, not that
        somebody remembered a call.
        """
        response = await client.post(
            f'/portal/team/{team["member_id"]}/revoke-sessions',
            headers=auth_header(team['admin_token']),
        )
        assert response.status_code == 200, response.text

        events = _audit_rows('/v1/portal/team/{user_id}/revoke-sessions')
        rows = [r for r in events if r['actor_user_id'] == team['admin_id']]
        assert rows, "withdrawing a member's sessions left no audit event"
        row = rows[0]
        assert row['method'] == 'POST'
        assert row['status_code'] == 200
        assert row['target_type'] == 'user'
        assert row['target_id'] == team['member_id'], 'the event names the wrong subject'
        assert row['org_id'] == team['org_id']

    async def test_a_refused_attempt_is_recorded_too(self, client, team):
        """The events an audit trail exists for are the ones that failed:
        an admin of another org probing user ids is exactly the pattern
        somebody would want to find afterwards."""
        outsider = f'ghost-{uuid.uuid4().hex[:8]}'
        await client.post(
            f'/portal/team/{outsider}/revoke-sessions',
            headers=auth_header(team['admin_token']),
        )
        rows = [r for r in _audit_rows('/v1/portal/team/{user_id}/revoke-sessions') if r['target_id'] == outsider]
        assert rows and rows[0]['status_code'] == 404, 'the refused attempt is not in the trail'


class TestTheCombinedWindowIsBounded:
    """How long the two margins cost *together*, with both really in play.

    `TestTheOneSecondWindow` pins each mechanism separately and its own
    docstring says they overlap on purpose. What nothing asserted is the
    **size of the overlap** — how long after a withdrawal somebody who
    signs straight back in still gets a 401. Measured against a running
    server: up to two seconds.

    Getting that under test needs real time, and the first attempt did
    not. It used `_set_watermark`, which writes the column directly and
    therefore *bypasses the rounding* — so it re-tested the comparison a
    class above already covers, and passed unchanged when the rounding
    was removed. A test of two mechanisms that only exercises one is the
    thing this suite keeps finding elsewhere.

    So this sleeps, once, for a second. That is the price of testing a
    property whose whole subject is a second.
    """

    async def test_a_token_minted_a_second_after_the_click_is_still_refused(self, client, team):
        """The overlap, with a real watermark.

        The revocation writes `trunc(now) + 1s`; a token minted a second
        later carries `iat == watermark`; `<=` refuses it. Remove either
        margin and this token is accepted — which is the point of
        asserting it here rather than trusting two separate tests to
        imply it.
        """
        import time

        member_id = team['member_id']
        response = await client.post('/portal/me/revoke-sessions', headers=auth_header(team['member_token']))
        assert response.status_code == 200, response.text

        watermark = _watermark_of(member_id)
        assert watermark is not None

        # Into the second the watermark names, so `iat == watermark`.
        time.sleep(max(0.0, watermark.timestamp() - datetime.now(timezone.utc).timestamp()) + 0.1)
        token = make_token(member_id)
        issued_at = JWTService().decode_token(token).issued_at
        assert issued_at == int(watermark.timestamp()), (
            f'the sleep did not land in the watermark second: iat {issued_at} vs '
            f'watermark {int(watermark.timestamp())} — this test is measuring nothing'
        )

        refused = await client.get('/portal/me', headers=auth_header(token))
        assert refused.status_code == 401, (
            'a token minted a second after the click was accepted, so the two margins no '
            'longer overlap. That may be a deliberate narrowing — but it changes how long '
            'a person is locked out, and the decision belongs here.'
        )

    async def test_the_lockout_ends_two_seconds_after_the_click(self, client, team):
        """The upper bound, computed from the real watermark rather than
        slept through: the watermark is never more than a second past the
        second the click happened in, so `iat = click_second + 2` is
        always accepted."""
        member_id = team['member_id']
        before = datetime.now(timezone.utc)
        response = await client.post('/portal/me/revoke-sessions', headers=auth_header(team['member_token']))
        assert response.status_code == 200, response.text

        watermark = _watermark_of(member_id)
        assert watermark is not None
        assert watermark.timestamp() <= int(before.timestamp()) + 2, (
            f'the watermark is {watermark.timestamp() - before.timestamp():.1f}s past the '
            'click — the lockout is longer than the two margins explain'
        )

        _set_watermark(member_id, watermark - timedelta(seconds=2))
        allowed = await client.get('/portal/me', headers=auth_header(make_token(member_id)))
        assert allowed.status_code == 200, allowed.status_code

    def test_the_cost_is_written_where_somebody_would_change_it(self):
        """A number that lives only in a test is a number nobody reads."""
        from app.db.sql.account_sessions import AccountSessionsDB

        doc = AccountSessionsDB.withdraw_sessions.__doc__ or ''
        assert 'second' in doc
        assert 'refused' in doc, (
            'the docstring no longer says a token minted in the second after the click is '
            'refused, which is what the two margins cost'
        )


class TestTheOneSecondWindow:
    """The rounding, from both sides. Without it there is a one-second
    hole at the only moment this feature is ever used."""

    async def test_the_watermark_is_a_whole_second_ahead_of_the_request(self, client, team):
        before = datetime.now(timezone.utc)
        response = await client.post('/portal/me/revoke-sessions', headers=auth_header(team['member_token']))
        assert response.status_code == 200, response.text

        watermark = _watermark_of(team['member_id'])
        assert watermark is not None
        assert watermark.microsecond == 0, f'the watermark is sub-second: {watermark!r}'
        assert watermark > before, (
            'the watermark is not ahead of the request, so a token minted in the same '
            f'second keeps working: watermark {watermark!r} vs request {before!r}'
        )

    async def test_a_token_minted_in_the_same_second_as_the_revocation_is_refused(self, client, team):
        """The hole itself, reproduced.

        The watermark is set to a sub-second time *within* the second the
        token's `iat` names — which is what a plain `now()` would have
        written — and the token must still be refused. `iat` is integer
        seconds, so truncation puts it strictly before any watermark
        inside the same second, and the comparison catches it.

        What this does *not* prove is the choice of `<=` over `<`: both
        refuse here, because the watermark is strictly greater. Checked,
        not assumed — an earlier version of this docstring claimed
        otherwise and a revert to `<` left the whole file green. The
        operator is pinned by the next test instead.
        """
        member_id = team['member_id']
        token = make_token(member_id)
        issued_at = JWTService().decode_token(token).issued_at
        assert issued_at is not None, 'the token carries no `iat`, so it can never be withdrawn'

        same_second = datetime.fromtimestamp(issued_at, tz=timezone.utc) + timedelta(milliseconds=400)
        _set_watermark(member_id, same_second)

        response = await client.get('/portal/me', headers=auth_header(token))
        assert response.status_code == 401, (
            'a token minted during the same second as the revocation survived it — '
            f'iat {issued_at} vs watermark {same_second!r}. Got {response.status_code}'
        )

    async def test_a_token_stamped_exactly_at_the_watermark_is_refused(self, client, team):
        """`<=`, not `<`, and this is the test that says so.

        Because the watermark is rounded up to a whole second and `iat`
        is whole seconds, exact equality is a case that really occurs: a
        revocation at 12:00:00.4 writes 12:00:01, and a token minted at
        12:00:01.7 carries `iat = 12:00:01`.

        Refusing it is the deliberate choice. Such a token was minted
        after the revocation instant, so `<` would not be *wrong* — but
        the sign-in that produced it may have been in flight when the
        operator clicked, and one second of over-refusal costs a person
        one extra sign-in while one second of under-refusal is the hole
        this whole feature exists to close. The two mechanisms overlap on
        purpose; neither is load-bearing alone.
        """
        member_id = team['member_id']
        token = make_token(member_id)
        issued_at = JWTService().decode_token(token).issued_at
        assert issued_at is not None

        _set_watermark(member_id, datetime.fromtimestamp(issued_at, tz=timezone.utc))

        response = await client.get('/portal/me', headers=auth_header(token))
        assert response.status_code == 401, (
            'a token stamped exactly at the watermark was accepted, so the comparison '
            f'is `<` rather than `<=`. iat {issued_at}, got {response.status_code}'
        )
