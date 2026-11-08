"""Deactivating a member deprovisions them, everywhere.

It did not. Measured against a running server: after an admin set a
member to `deactivated`, that member's existing token still read
`/v1/artifacts/shared`, read `/v1/notifications`, **published a new
document and appended a version to it**. Only `/v1/portal/*` refused
them, because the portal's own dependency was the one thing that read
the membership row.

Membership was the wrong place for the fact, twice over. Every account
is given a personal organisation at sign-up, so deactivating the
company membership left an active one beside it; and documents are
owned per user rather than per organisation, so a person's work was
never the organisation's to withdraw.

So it is recorded on the account and checked at the one point every
authenticated request already passes — and refused at sign-in too,
because the door only sees tokens and a login page would otherwise
undo it.
"""

from __future__ import annotations

import uuid

import anyio
import pytest
from sqlalchemy import create_engine, text

from tests.conftest import (
    add_member,
    auth_header,
    create_org,
    create_user,
    make_token,
    TEST_DB_URL_SYNC,
)

pytestmark = pytest.mark.anyio

PAGE = '<!doctype html><html><body><h1>After I left</h1></body></html>'


def _deactivate(user_id: str) -> None:
    """What the admin endpoint does, reached directly.

    Synchronous for the reason `test_audit_trail.py` documents: the
    `db` and `client` fixtures run on different event loops.
    """
    engine = create_engine(TEST_DB_URL_SYNC)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    'UPDATE users SET deactivated_at = now(), '
                    "tokens_valid_after = date_trunc('second', now()) + interval '1 second' "
                    'WHERE user_id = :u'
                ),
                {'u': user_id},
            )
    finally:
        engine.dispose()


def _reactivate(user_id: str) -> None:
    """Clear the mark, leaving the withdrawal watermark where it is."""
    engine = create_engine(TEST_DB_URL_SYNC)
    try:
        with engine.begin() as conn:
            conn.execute(
                text('UPDATE users SET deactivated_at = NULL WHERE user_id = :u'),
                {'u': user_id},
            )
    finally:
        engine.dispose()


@pytest.fixture
async def leaver(db):
    """Somebody with an account, an org, and a token in their pocket."""
    user = await create_user(db, email=f'leaver-{uuid.uuid4().hex[:8]}@acme.test')
    org = await create_org(db, owner_user_id=str(user.user_id), slug=f'acme-{uuid.uuid4().hex[:6]}')
    await add_member(db, str(org.id), str(user.user_id), role='member')
    return {'user_id': str(user.user_id), 'email': str(user.email), 'token': make_token(str(user.user_id))}


class TestTheTokenTheyAlreadyHold:
    """Measured on `/notifications`, which used to be `/artifacts/shared`.

    Documents left for `igni__artifacts`, so the route this reached
    for is not here any more. The subject has not changed: what is
    being tested is `require_existing_user` refusing a deactivated
    account, and any authenticated route exercises it.

    **The document case is not untested, it moved.** The premium
    service cannot see `deactivated_at` either — it has no `users`
    table — so it asks, and
    `igni__artifacts/tests/test_a_departed_employee_loses_the_documents.py`
    holds that end. Without it, a deactivated person kept reading
    documents for the 30 days their token had left.
    """

    async def test_it_stops_reading(self, client, leaver):
        before = await client.get('/notifications', headers=auth_header(leaver['token']))
        assert before.status_code == 200, 'the fixture is not measuring anything'

        _deactivate(leaver['user_id'])

        after = await client.get('/notifications', headers=auth_header(leaver['token']))
        assert after.status_code == 401

    async def test_it_stops_writing(self, client, leaver):
        # A write, not just a read: the account is refused before the
        # body is even looked at.
        _deactivate(leaver['user_id'])

        response = await client.post(
            '/notifications/read',
            headers=auth_header(leaver['token']),
            json={},
        )

        assert response.status_code == 401

    async def test_it_stops_reading_notifications(self, client, leaver):
        _deactivate(leaver['user_id'])

        assert (await client.get('/notifications', headers=auth_header(leaver['token']))).status_code == 401

    async def test_the_refusal_says_nothing_about_why(self, client, leaver):
        # The same wording as a bad signature. A caller learns their
        # token no longer works, which they need, and nothing else.
        _deactivate(leaver['user_id'])

        response = await client.get('/notifications', headers=auth_header(leaver['token']))

        assert 'deactivated' not in response.text.lower()


class TestASweepOverTheWholeSurface:
    """Route by route, from the router rather than from a list.

    The same shape as the deleted-user sweep next door, and for the
    same reason: a check that lives at one choke point is only worth
    what the sweep proves about every route that passes through it.
    """

    async def test_every_user_authenticated_route_refuses_them(self, client, leaver, db):
        from tests.test_a_deleted_user_cannot_come_back import (
            _ABSOLUTE,
            _DELIBERATELY_NOT_REFUSED,
            _fill,
            _user_authenticated_routes,
            _VALID_REQUESTS,
        )

        _deactivate(leaver['user_id'])
        headers = auth_header(leaver['token'])

        routes = _user_authenticated_routes()
        assert len(routes) > 30, f'only {len(routes)} routes found — has the router changed shape?'

        allowed: list[str] = []
        for method, path in routes:
            if (method, path) in _DELIBERATELY_NOT_REFUSED:
                continue
            request = _VALID_REQUESTS.get((method, path), {})
            response = await client.request(
                method,
                # Absolute: the openapi paths already carry `/v1` and so
                # does the fixture's base URL.
                _ABSOLUTE + _fill(path),
                headers=headers,
                json=request.get('json', {}),
                params=request.get('params'),
            )
            if response.status_code in (401, 422):
                continue
            allowed.append(f'{method} {path} -> {response.status_code}')

        assert not allowed, (
            'these served somebody who has been deprovisioned:\n  '
            + '\n  '.join(allowed)
            + '\n\nDeactivation is recorded on the account and enforced in '
            '`app.security.subject.Subjects.require_existing_user`. A route reaching the handler '
            'without passing through it is a route an ex-employee keeps.'
        )


class TestSigningInAgain:
    """The door only sees tokens, so sign-in has to refuse too."""

    async def test_a_password_sign_in_is_refused(self, client, db):
        import bcrypt

        from tests.conftest import create_user_with_password

        password = 'correct-horse-battery'
        user = await create_user_with_password(db, password=password, email=f'pw-{uuid.uuid4().hex[:8]}@test.com')
        assert bcrypt.checkpw(password.encode(), str(user.hashed_password).encode())
        _deactivate(str(user.user_id))

        response = await client.post('/sign-in', json={'email': str(user.email), 'password': password})

        assert response.status_code == 403
        # Said plainly: the credential was accepted and the account is
        # not, so "invalid password" would send somebody to reset one
        # that works.
        assert 'deactivated' in response.json()['detail'].lower()

    async def test_the_password_itself_is_still_correct(self, client, db):
        # Guards the test above from passing for the wrong reason.
        from tests.conftest import create_user_with_password

        password = 'correct-horse-battery'
        user = await create_user_with_password(db, password=password, email=f'pw2-{uuid.uuid4().hex[:8]}@test.com')

        ok = await client.post('/sign-in', json={'email': str(user.email), 'password': password})

        assert ok.status_code == 200

    async def test_a_provider_sign_in_is_refused(self, db):
        from fastapi import HTTPException

        from app.services.auth.oauth_service import OAuthService, OAuthUserInfo

        user = await create_user(db, email=f'sso-{uuid.uuid4().hex[:8]}@test.com')
        _deactivate(str(user.user_id))

        with pytest.raises(HTTPException) as refused:
            await OAuthService.find_or_create_user_and_get_token(
                OAuthUserInfo(email=str(user.email), first_name='Departed', last_name='Person', provider='oidc')
            )

        assert refused.value.status_code == 403


class TestComingBack:
    async def test_reactivating_lets_them_in_again(self, client, leaver, db):
        _deactivate(leaver['user_id'])
        _reactivate(leaver['user_id'])

        # Past the withdrawal watermark, which deactivation set to the
        # next whole second — and equal counts as revoked, because
        # `iat` is whole seconds and a token minted in the same second
        # would otherwise survive the revocation meant to kill it. So
        # the wait has to clear that second, not land on it.
        #
        # Waited rather than clearing the watermark to make the test
        # pass: the watermark surviving reactivation is the behaviour
        # the next test depends on.
        await anyio.sleep(2.2)
        fresh = make_token(leaver['user_id'])

        assert (await client.get('/notifications', headers=auth_header(fresh))).status_code == 200

    async def test_but_not_with_the_tokens_they_held_before(self, client, leaver):
        # Coming back is not a reason to re-admit the token on the
        # laptop that was the reason they went.
        old = leaver['token']
        _deactivate(leaver['user_id'])
        _reactivate(leaver['user_id'])

        assert (await client.get('/notifications', headers=auth_header(old))).status_code == 401
