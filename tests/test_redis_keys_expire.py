"""Every short-lived Redis key still expires after the `setex` rewrite.

redis-py 8.1 deprecates `setex(key, ttl, value)` in favour of
`set(key, value, ex=ttl)` — which reorders the arguments. Both ways of
getting that wrong were planted to find out which the existing suite
already covers, and they are not the same:

* **Wrong order** (`set(key, ttl, code)`) — already caught. The stored
  value becomes `600`, so five tests in `test_otp_hardening.py` fail.
* **Dropped expiry** (`set(key, code)`) — **silent**. The value is
  correct and the code verifies immediately, so all 18 tests in
  `test_otp_hardening.py` and `test_no_secrets_in_responses.py` pass
  against a sign-in code that never expires. Only an assertion on the
  recorded TTL sees it (`ttl=-1`).

That second case is the reason this file exists. A six-digit code with a
five-guess allowance that lives forever instead of ten minutes is a
materially different thing to attack.

All five rewritten sites were verified against live Redis before the
rewrite was committed — against the *configured* database, not db 0. The
first probe read db 0 and reported `ttl=-2` for a key that was written
correctly to db 10, which looks identical to the bug it was meant to
catch.
"""

from __future__ import annotations

import pytest
import redis.asyncio as aioredis

from app.config import settings


@pytest.fixture
async def r():
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    yield client
    await client.aclose()


EMAIL = 'ttl-probe@example.com'


class TestOneTimeCodes:
    """All three purposes, because the rewrite touched each separately and
    a single missed keyword would only show up on one of them."""

    @pytest.mark.parametrize(
        'purpose,method',
        [
            ('signup', 'generate_signup_code'),
            ('signin', 'generate_signin_code'),
            ('reset', 'generate_password_reset_code'),
        ],
    )
    async def test_the_code_expires_and_is_stored_sealed(self, r, purpose, method):
        from app.services.auth.otp_service import _OTP_TTL_SECONDS, OTPService

        key = f'otp:{purpose}:{EMAIL}'
        await r.delete(key)

        result = await getattr(OTPService(), method)(EMAIL)

        ttl = await r.ttl(key)
        stored = await r.get(key)
        await r.delete(key)

        # -1 is "exists, no expiry" and -2 is "no such key"; both mean the
        # argument order is wrong, and both are invisible to every other test.
        assert ttl > 0, f'{purpose}: ttl={ttl} — the code does not expire'
        assert abs(ttl - _OTP_TTL_SECONDS) <= 5, f'{purpose}: ttl={ttl}, want ~{_OTP_TTL_SECONDS}'
        # The other half of the swap: the value must be the code, not the
        # TTL — encrypted since F151, so never the code as given.
        from app.db.redis.sealed import unseal

        assert stored is not None and stored != result.code, f'{purpose}: the code is stored in plaintext'
        assert unseal(stored) == result.code, f'{purpose}: stored {stored!r}, issued {result.code!r}'
        assert result.code.isdigit() and len(result.code) == 6


class TestOAuthState:
    async def test_it_expires_and_still_round_trips(self, r):
        """The CSRF state for the OIDC login. No expiry means a state
        parameter stays replayable indefinitely — except it is also
        single-use, which is asserted here so the two protections are not
        confused for each other.
        """
        from app.db.redis.oauth_state import _PREFIX, _TTL_SECONDS, OAuthStateDB
        from app.db.redis.sealed import digest

        key = f'{_PREFIX}{digest("ttl-probe")}'
        await r.delete(key)

        await OAuthStateDB().store('ttl-probe', {'a': '1'})
        ttl = await r.ttl(key)

        assert ttl > 0, f'ttl={ttl} — oauth state does not expire'
        assert abs(ttl - _TTL_SECONDS) <= 5

        consumed = await OAuthStateDB().validate_and_consume('ttl-probe')

        assert consumed == {'a': '1'}, 'the value did not survive the rewrite'
        assert not await r.exists(key), 'state must be single-use'


class TestNoCallerRegresses:
    def test_setex_is_gone_from_the_app(self):
        """Pinned as a rule rather than per-call-site, so a new `setex`
        anywhere fails here with the reason attached instead of adding a
        deprecation warning nobody reads.
        """
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent / 'app'
        offenders = [str(p.relative_to(root.parent)) for p in root.rglob('*.py') if '.setex(' in p.read_text()]

        assert not offenders, (
            f'`setex` is deprecated in redis-py 8; use `set(key, value, ex=ttl)` '
            f'— note the argument order changes. Found in: {offenders}'
        )
