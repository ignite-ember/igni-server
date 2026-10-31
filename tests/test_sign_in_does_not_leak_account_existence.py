"""Password sign-in answers 401 for every failure, and takes the same time.

`AuthService.verify_password` called `bcrypt.checkpw` directly, and that
raises `ValueError: Invalid salt` when the stored value is not a bcrypt
hash. `UserDB.ensure_exists` stores `''` — so **every account created
through OIDC, OAuth or an OTP sign-up that never set a password** turned
`POST /v1/sign-in` into a 500.

Verified against a running server before the fix:

    nobody-at-all@example.com   status=401
    alex@igni.sh                status=500      <- registered, no password

So the status code alone was an account-existence oracle: 401 meant "no
such address", 500 meant "registered". That is the same thing the OTP
hardening work (F28, F29) was protecting, reachable without a single
guess. Also a plain bug — a user who types their email and any password
into the form gets a server error rather than "wrong password".

The clock leaked too, in a different direction. Pre-fix, only an account
*with* a password paid for bcrypt; every other outcome short-circuited.
Measured after the fix, all four outcomes sit together:

    no such account                    401   0.1705s
    account exists, no password        401   0.1670s
    account exists, wrong password     401   0.1675s
    account exists, CORRECT password   200   0.1672s

Wall-clock timings are not asserted here — they are far too noisy in CI to
be a gate. What is asserted is the mechanism that produces them: that a
bcrypt comparison happens even when there is no hash to compare against.
"""

from __future__ import annotations

import bcrypt
import pytest

from app.services.auth.auth_service import AuthService


async def _create_passwordless(db, *, user_id: str, email: str):
    """Insert a user the way `UserDB.ensure_exists` does — empty hash.

    Not `conftest.create_user` plus a mutation: that helper defaults to
    `'$2b$12$fakehash'`, and the app reads through a different session, so
    an in-test reassignment was invisible to the endpoint. The first
    version of these tests passed while exercising a malformed hash rather
    than the empty one the product actually stores — both raise, so the
    difference was silent.
    """
    from datetime import datetime, timezone
    import uuid

    from app.db.sql.models.user import User

    now = datetime.now(timezone.utc)
    db.add(
        User(
            id=str(uuid.uuid4()),
            user_id=user_id,
            email=email,
            hashed_password='',
            full_name='No Password',
            auth_provider='email',
            created_at=now,
            updated_at=now,
        )
    )
    await db.flush()
    return email


class TestVerifyPasswordNeverRaises:
    """The unit that caused it. `''` is the value the product actually
    stores, so it leads."""

    @pytest.mark.parametrize(
        'stored',
        [
            '',  # what ensure_exists writes
            '   ',
            'not-a-hash',
            'plaintextpassword',
            '$2b$12$tooshort',
            '$1$md5$whatever',  # a real crypt hash, wrong family
            'null',
        ],
    )
    def test_an_unusable_hash_is_a_failed_check_not_an_error(self, stored: str):
        assert AuthService.verify_password('anything', stored) is False

    def test_a_real_hash_still_verifies_both_ways(self):
        """The guard must not turn into "always False"."""
        hashed = AuthService.hash_password('correcthorse')

        assert AuthService.verify_password('correcthorse', hashed) is True
        assert AuthService.verify_password('wrong', hashed) is False


class TestTheEndpointStopsDistinguishing:
    @pytest.fixture
    async def passwordless(self, db):
        """A user created the way OIDC / OAuth / OTP sign-up creates one."""
        return await _create_passwordless(db, user_id='nopw-1', email='nopw@example.com')

    async def test_a_passwordless_account_gets_401_not_500(self, client, passwordless):
        response = await client.post('/sign-in', json={'email': passwordless, 'password': 'anything'})

        assert response.status_code == 401, response.text

    async def test_the_two_answers_are_byte_identical(self, client, passwordless):
        """The oracle was the *difference*, so the assertion is on the
        difference rather than on either value."""
        missing = await client.post('/sign-in', json={'email': 'no-such-address@example.com', 'password': 'anything'})
        registered = await client.post('/sign-in', json={'email': passwordless, 'password': 'anything'})

        assert missing.status_code == registered.status_code == 401
        assert missing.json() == registered.json()


class TestTheTimingIsAbsorbed:
    """A status code that matches while the clock does not is half a fix.

    Asserted by counting bcrypt comparisons rather than by measuring
    seconds — the property is "work happens on every path", and a timing
    assertion would be flaky in CI for the same reason it is convincing
    by hand.
    """

    def test_a_missing_hash_still_costs_a_bcrypt_comparison(self, monkeypatch):
        calls: list[bytes] = []
        real = bcrypt.checkpw

        def counting(password: bytes, hashed: bytes) -> bool:
            calls.append(hashed)
            return real(password, hashed)

        monkeypatch.setattr(bcrypt, 'checkpw', counting)

        AuthService._absorb_timing('some-password')

        assert len(calls) == 1, 'no comparison happened — the timing gap is back'
        assert calls[0].startswith(b'$2b$'), calls[0]

    @pytest.mark.parametrize('email', ['no-such-address@example.com', 'nopw@example.com'])
    async def test_sign_in_itself_pays_the_cost_on_every_failing_path(self, client, monkeypatch, email):
        """Calls `_absorb_timing` through the endpoint, not directly.

        Testing the helper alone passes even if `sign_in` stops calling it
        — which is exactly what a later refactor would do. This asserts
        the wiring: a comparison happens for a missing account and for a
        registered one with no password.
        """
        await _create_passwordless(self._db, user_id='nopw-2', email='nopw@example.com')

        # Counting *calls* is not enough. On the passwordless path
        # `verify_password` already calls `checkpw` with `b''`, which raises
        # immediately and costs nothing — indistinguishable from real work
        # by call count alone. Only a comparison against a genuine bcrypt
        # hash actually spends the time, so that is what is counted.
        real_comparisons = 0
        real = bcrypt.checkpw

        def counting(password: bytes, hashed: bytes) -> bool:
            nonlocal real_comparisons
            result = real(password, hashed)  # raises on a malformed hash
            real_comparisons += 1
            return result

        monkeypatch.setattr(bcrypt, 'checkpw', counting)

        response = await client.post('/sign-in', json={'email': email, 'password': 'anything'})

        assert response.status_code == 401
        assert real_comparisons >= 1, (
            f'{email}: no comparison against a real bcrypt hash, so this path '
            f'returns in ~1ms while a wrong password takes ~170ms'
        )

    @pytest.fixture(autouse=True)
    def _capture_db(self, db):
        """`db` is needed by the endpoint test above but not the unit ones;
        held on the instance so the signature stays readable."""
        self._db = db

    def test_the_dummy_hash_is_built_once(self, monkeypatch):
        """It is deliberately lazy: bcrypt is slow by design and startup
        should not pay for it. But it must not be regenerated per request
        either, which would double the cost of every failed sign-in."""
        AuthService._dummy_hash = None
        try:
            generated = 0
            real_hashpw = bcrypt.hashpw

            def counting_hashpw(password: bytes, salt: bytes) -> bytes:
                nonlocal generated
                generated += 1
                return real_hashpw(password, salt)

            monkeypatch.setattr(bcrypt, 'hashpw', counting_hashpw)

            AuthService._absorb_timing('a')
            AuthService._absorb_timing('b')
            AuthService._absorb_timing('c')

            assert generated == 1, f'dummy hash rebuilt {generated} times'
        finally:
            AuthService._dummy_hash = None


class TestNoOtherCallerBypassesTheGuard:
    def test_checkpw_is_not_called_raw_on_a_user_password(self):
        """`verify_password` is the only place a *user* password meets
        bcrypt. The super-admin and deployment-admin paths have their own
        guarded comparisons (F23) and are allowlisted by name.
        """
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent / 'app'
        allowed = {'services/auth/auth_service.py', 'security/super_admin.py', 'security/deployment_admin.py'}

        offenders = [
            str(p.relative_to(root))
            for p in root.rglob('*.py')
            if 'bcrypt.checkpw' in p.read_text() and str(p.relative_to(root)) not in allowed
        ]

        assert not offenders, (
            f'bcrypt.checkpw called outside the guarded helpers: {offenders}. A raw '
            f'call raises ValueError on a stored value that is not a bcrypt hash, '
            f'which becomes a 500 and an account-existence oracle.'
        )
