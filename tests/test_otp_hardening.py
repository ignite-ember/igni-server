"""A six-digit code that signs you in, and what it takes to guess one.

Two things were wrong, and both were measured rather than inferred.

**The code was predictable in principle.** ``random.choices`` is the
Mersenne Twister, whose state is recoverable from enough outputs — and
the outputs here are handed to whoever asks, so an attacker can collect
them for their own address and then predict somebody else's.

**And guessable in practice.** Six digits is a million possibilities with
a ten-minute life, and there was no attempt counter at all: 300 wrong
guesses in a row all answered 401 and the real code still worked
afterwards. With no rate limiting anywhere in this product — a deliberate
decision made elsewhere — that window is enough to walk a large fraction
of the space.

The remedy for the second is to throw the code away after five failures,
not to lock the account. Locking would hand an attacker a
denial-of-service against any address they know; deleting the code costs
the real user one more email, and asking for that email goes through the
mail path.
"""

from __future__ import annotations

from fastapi import HTTPException
import pytest

from app.services.auth.otp_service import _MAX_ATTEMPTS, OTPService
from tests.conftest import create_user

EMAIL = 'victim@test.com'


@pytest.fixture(autouse=True)
async def _clear_send_throttle():
    """These tests are about guessing a code, not about sending one.

    F112 added a real production limit on how often an anonymous caller
    can make the deployment send mail — ten per address per hour, plus a
    cooldown while a code is pending. This file issues far more than that
    for one shared address, because it exercises the attempt allowance
    and the replay guard.

    So the send counters are cleared around each test. The limit itself is
    covered by `test_anonymous_email_cannot_be_abused.py`; mixing the two
    concerns here would make every assertion below depend on how many
    codes the previous test happened to request.
    """
    import redis.asyncio as aioredis

    from app.config import settings

    async def _clear():
        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            keys = [key async for key in client.scan_iter('code_throttle:*')]
            if keys:
                await client.delete(*keys)
        finally:
            await client.aclose()

    await _clear()
    yield
    await _clear()


OTHER = 'bystander@test.com'


@pytest.fixture
async def users(db):
    await create_user(db, user_id='v1', email=EMAIL)
    await create_user(db, user_id='v2', email=OTHER)


async def _wrong(email: str, n: int) -> None:
    """*n* wrong guesses, each of which must be refused."""
    for i in range(n):
        with pytest.raises(HTTPException) as caught:
            await OTPService().validate_and_signin_with_code(email, f'{i:06d}')
        assert caught.value.status_code == 401


class TestTheGeneratorIsCryptographic:
    def test_it_does_not_use_the_random_module(self):
        """Asserted on the source: both generators produce six digits, so
        no output-based test can tell them apart."""
        import inspect

        from app.services.auth import otp_service

        source = inspect.getsource(otp_service)

        assert 'secrets.choice' in source
        assert 'random.choices' not in source

    def test_codes_are_six_digits(self):
        from app.services.auth.otp_service import _generate_code

        for _ in range(20):
            code = _generate_code()
            assert len(code) == 6
            assert code.isdigit()

    def test_codes_differ(self):
        """Not a randomness test — a wiring one. A generator returning a
        constant would satisfy every other assertion here."""
        from app.services.auth.otp_service import _generate_code

        assert len({_generate_code() for _ in range(50)}) > 1


class TestGuessingCostsTheCode:
    async def test_the_code_dies_after_the_allowance(self, users):
        result = await OTPService().generate_signin_code(email=EMAIL)

        await _wrong(EMAIL, _MAX_ATTEMPTS)

        # The real code is gone: the attacker has to make the real user
        # ask for another one.
        with pytest.raises(HTTPException) as caught:
            await OTPService().validate_and_signin_with_code(EMAIL, result.code)
        assert caught.value.status_code == 401

    async def test_the_allowance_is_not_so_tight_it_hurts_typing(self, users):
        """Four wrong guesses then the right one still signs in. A limit
        of one would be safer and unusable."""
        result = await OTPService().generate_signin_code(email=EMAIL)

        await _wrong(EMAIL, _MAX_ATTEMPTS - 1)

        assert (await OTPService().validate_and_signin_with_code(EMAIL, result.code)).access_token

    async def test_a_fresh_code_gets_a_fresh_allowance(self, users):
        """Otherwise an earlier burst of failures kills the new code on
        arrival, and the account is effectively locked — the outcome the
        delete-the-code design exists to avoid."""
        await OTPService().generate_signin_code(email=EMAIL)
        await _wrong(EMAIL, _MAX_ATTEMPTS)

        second = await OTPService().generate_signin_code(email=EMAIL)

        assert (await OTPService().validate_and_signin_with_code(EMAIL, second.code)).access_token

    async def test_one_persons_failures_do_not_touch_another(self, users):
        """The counter is keyed by email *and* purpose.

        A shared counter would let anybody disable everybody else's
        sign-in by guessing at their own address: once the shared
        allowance is spent, the very next failure against any other
        address throws that address's code away.

        Getting this to actually bite took two corrections, both found by
        reverting the fix and watching the test pass anyway. Checking only
        that the victim's code still worked was not enough — burning the
        allowance elsewhere deletes only the code being guessed at. And
        spending the *whole* allowance was not enough either, because the
        limit deletes the counter along with the code, so a shared counter
        resets itself.
        """
        victim = await OTPService().generate_signin_code(email=EMAIL)
        await OTPService().generate_signin_code(email=OTHER)

        # Somebody else spends the allowance to one short of the limit.
        #
        # One short, not all of it: at the limit the code and its counter
        # are deleted together, so a shared counter would reset itself and
        # the test would pass against the very thing it is checking. Left
        # at four, a shared counter is primed — and the victim's next
        # single failure trips it.
        await _wrong(OTHER, _MAX_ATTEMPTS - 1)

        # One fat-fingered attempt by the victim must not be fatal.
        await _wrong(EMAIL, 1)

        assert (await OTPService().validate_and_signin_with_code(EMAIL, victim.code)).access_token


class TestSuccessConsumesTheCode:
    async def test_a_code_cannot_be_replayed(self, users):
        result = await OTPService().generate_signin_code(email=EMAIL)

        assert (await OTPService().validate_and_signin_with_code(EMAIL, result.code)).access_token

        with pytest.raises(HTTPException):
            await OTPService().validate_and_signin_with_code(EMAIL, result.code)


class TestEveryPurposeIsCovered:
    """Three code paths shared the same shape, so a fix to one of them is
    the kind of thing that reads as done and is not."""

    async def test_signup_counts_attempts(self, users):
        result = await OTPService().generate_signup_code(email=EMAIL)
        for i in range(_MAX_ATTEMPTS):
            with pytest.raises(HTTPException):
                await OTPService().validate_signup_code(EMAIL, f'{i:06d}')

        with pytest.raises(HTTPException):
            await OTPService().validate_signup_code(EMAIL, result.code)

    async def test_password_reset_counts_attempts(self, users):
        """The one that matters most: a guessed reset code is the account."""
        result = await OTPService().generate_password_reset_code(email=EMAIL)
        for i in range(_MAX_ATTEMPTS):
            with pytest.raises(HTTPException):
                await OTPService().reset_password_with_code(EMAIL, f'{i:06d}', 'new-password-123')

        with pytest.raises(HTTPException):
            await OTPService().reset_password_with_code(EMAIL, result.code, 'new-password-123')

    async def test_a_reset_still_works_within_the_allowance(self, users):
        result = await OTPService().generate_password_reset_code(email=EMAIL)

        with pytest.raises(HTTPException):
            await OTPService().reset_password_with_code(EMAIL, '000000', 'new-password-123')

        message = await OTPService().reset_password_with_code(EMAIL, result.code, 'new-password-123')
        assert 'reset' in message.lower()
