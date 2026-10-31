"""Four endpoints let an anonymous caller make us send mail. F112.

`/v1/send-sign-up-code`, `/v1/send-sign-in-code`,
`/v1/send-reset-password-code` and the welcome mail from `/v1/sign-up`
all take an address from an unauthenticated request and send to it.
Nothing limited them, and one of them answered differently depending on
whether the address had an account.

Both were demonstrated before being fixed:

    registered address -> 400 {"detail":"User already registered"}
    unknown address    -> 200 {"message":"..."}
    DISTINGUISHABLE: True

    25 anonymous requests for one address -> statuses {200}
    every one accepted: 25/25

The first is account enumeration, unauthenticated and unlimited — the
same class F44 closed on the sign-in path, still open here, and directly
contradicting the comment in the endpoint file promising the response
"is identical whether or not the address belongs to a real account".

The second is two problems. The mailbox owner is griefed: every request
replaces the pending code, so a person typing theirs in has it
invalidated. And the deployment becomes a mail cannon pointed at any
address, sending from the customer's own relay identity — which is how a
sending domain gets blacklisted and an SES account suspended.

**This is not the rate limiting that was deliberately removed.** Nothing
here limits what an authenticated user does with inference. This limits
how often an anonymous caller can make the server take an outbound
action against somebody else's mailbox.
"""

from __future__ import annotations

import pathlib
import uuid

import pytest
import redis.asyncio as aioredis
from sqlalchemy import create_engine, text

from app.config import settings
from app.monitoring import metrics as monitoring
from app.services.auth.auth_service import AuthService
from app.services.auth.code_throttle import COOLDOWN_SECONDS, PER_ADDRESS_HOURLY
from tests.conftest import TEST_DB_URL_SYNC

_ROOT = pathlib.Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.anyio

_SENDERS = ('/send-sign-up-code', '/send-sign-in-code', '/send-reset-password-code')


def _address() -> str:
    return f'probe-{uuid.uuid4().hex[:10]}@example.com'


def _register(email: str) -> None:
    engine = create_engine(TEST_DB_URL_SYNC)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    'INSERT INTO users (id, user_id, email, hashed_password, full_name, auth_provider,'
                    " created_at, updated_at) VALUES (:i, :u, :e, :h, 'Real', 'email', now(), now())"
                ),
                {
                    'i': str(uuid.uuid4()),
                    'u': str(uuid.uuid4()),
                    'e': email,
                    'h': AuthService.hash_password('Whatever-1!'),
                },
            )
    finally:
        engine.dispose()


async def _stored_code(purpose: str, email: str) -> str | None:
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        value: str | None = await client.get(f'otp:{purpose}:{email}')
        # Sealed since F151; the test reads what the user was sent.
        from app.db.redis.sealed import unseal

        return unseal(value) if value is not None else None
    finally:
        await client.aclose()


def _throttled_total() -> float:
    total = 0.0
    for metric in monitoring.REGISTRY.collect():
        if metric.name == 'igni_email_codes_throttled':
            total += sum(s.value for s in metric.samples if s.name.endswith('_total'))
    return total


class TestNoAddressCanBeEnumerated:
    async def test_sign_up_answers_the_same_for_a_registered_address(self, client):
        """The oracle. `400 User already registered` against `200` told an
        anonymous caller whether any address had an account."""
        registered, unknown = _address(), _address()
        _register(registered)

        known = await client.post('/send-sign-up-code', json={'email': registered})
        fresh = await client.post('/send-sign-up-code', json={'email': unknown})

        assert known.status_code == fresh.status_code, (known.status_code, fresh.status_code)
        assert known.json() == fresh.json(), (known.text, fresh.text)

    async def test_no_code_is_delivered_for_a_registered_address(self, client):
        """Identical *responses* must not mean identical *behaviour*:
        sending a sign-up code to somebody who already has an account is
        a confusing email at best."""
        registered = _address()
        _register(registered)

        response = await client.post('/send-sign-up-code', json={'email': registered})

        assert response.status_code == 200
        # A code is still generated and stored — the work is identical on
        # both paths, so the two do not differ in timing — and it buys
        # nothing, because `sign_up` refuses with a 409 for an existing
        # account after validating it.
        assert await _stored_code('signup', registered) is not None

    async def test_a_stolen_sign_up_code_still_cannot_take_over_an_account(self, client):
        """The reason storing it is safe. If this ever stops being true,
        suppressing delivery is not enough on its own."""
        registered = _address()
        _register(registered)
        await client.post('/send-sign-up-code', json={'email': registered})
        code = await _stored_code('signup', registered)

        response = await client.post(
            '/sign-up',
            json={
                'email': registered,
                'code': code,
                'password': 'Attacker-Chosen-1!',
                'first_name': 'A',
                'last_name': 'B',
            },
        )

        assert response.status_code == 409, response.text

    @pytest.mark.parametrize('path', _SENDERS)
    async def test_an_unknown_address_is_accepted_everywhere(self, client, path):
        """None of the three may refuse an address for not existing —
        that refusal *is* the oracle."""
        response = await client.post(path, json={'email': _address()})

        assert response.status_code == 200, response.text


class TestTheSendsAreLimited:
    @pytest.mark.parametrize('path,purpose', list(zip(_SENDERS, ('signup', 'signin', 'reset'))))
    async def test_a_second_immediate_request_sends_nothing(self, client, path, purpose):
        """The cooldown. A double-clicked button, and the first half of
        the griefing defence."""
        email = _address()
        before = _throttled_total()

        first = await client.post(path, json={'email': email})
        issued = await _stored_code(purpose, email)
        second = await client.post(path, json={'email': email})

        assert first.status_code == second.status_code == 200
        assert _throttled_total() > before, 'the second request was not throttled'
        assert await _stored_code(purpose, email) == issued, 'the pending code was replaced'

    async def test_the_first_code_stays_valid_under_a_flood(self, client):
        """The griefing this closes. Every request used to replace the
        pending code, so an attacker could keep a legitimate user from
        ever completing a sign-in."""
        email = _address()

        await client.post('/send-sign-in-code', json={'email': email})
        original = await _stored_code('signin', email)

        for _ in range(10):
            await client.post('/send-sign-in-code', json={'email': email})

        assert (
            await _stored_code('signin', email) == original
        ), 'a flood of anonymous requests rotated the code out from under the user'

    async def test_a_throttled_send_is_indistinguishable_from_a_sent_one(self, client):
        """A 429 would say "somebody asked for a code for this address
        recently", which is an activity oracle on endpoints designed to
        say nothing about an address."""
        email = _address()

        first = await client.post('/send-sign-in-code', json={'email': email})
        second = await client.post('/send-sign-in-code', json={'email': email})

        assert first.status_code == second.status_code
        assert first.json() == second.json(), (first.text, second.text)

    def test_the_limits_are_not_vacuous(self):
        """A cooldown of zero or a cap of a million would make every test
        above pass while limiting nothing."""
        assert 10 <= COOLDOWN_SECONDS <= 300, COOLDOWN_SECONDS
        assert 1 <= PER_ADDRESS_HOURLY <= 20, PER_ADDRESS_HOURLY

    def test_the_global_cap_can_be_switched_off(self):
        """An organisation onboarding three hundred people must not find
        sign-in switched off, and that failure is worse than the abuse
        the cap prevents."""
        assert settings.email_codes_hourly_limit > 0, 'the default should be a generous cap, not off'

        field = type(settings).model_fields['email_codes_hourly_limit']
        assert '0 disables' in (field.description or ''), field.description


class TestNothingCanBypassTheThrottle:
    """The rule. Guarding the endpoints would leave the next endpoint
    free to forget, so the guard is in the generators — and this asserts
    that every generator has it."""

    async def test_every_code_generator_consults_the_throttle(self, monkeypatch):
        """Called, not read.

        This was an AST rule requiring the strings `allow` and
        `CodeSendThrottled` inside every `generate_*` body. It broke
        the moment the three generators stopped each carrying their own
        copy of the guard and started delegating to one that has it —
        the property was intact and the rule said otherwise.

        A rule that reads source can only ever recognise the spellings
        its author thought of, which this repository has now been
        bitten by three times (F80, the log-hygiene guard, and the PKCE
        pair). So: deny at the throttle, call each generator, and
        require every one of them to refuse. Delegation passes,
        reimplementation passes, and a generator that forgets the
        guard cannot.

        The list is discovered rather than written down, so a *new*
        generator is covered the day it is added — which is the part
        the AST rule got right.
        """
        from app.services.auth.code_throttle import CodeThrottle
        from app.services.auth.otp_service import CodeSendThrottled, OtpPurpose, OTPService

        consulted: list[str] = []

        async def _deny(_self, *, purpose: str, email: str, code_pending: bool) -> bool:
            consulted.append(purpose)
            return False

        monkeypatch.setattr(CodeThrottle, 'allow', _deny)

        generators = sorted(name for name in dir(OTPService) if name.startswith('generate_'))
        assert len(generators) >= 4, generators

        service = OTPService()
        for name in generators:
            # `generate_code` is the one that takes the purpose; the
            # rest name it. Spelled out rather than introspected, so a
            # generator with a third shape fails here loudly instead of
            # being skipped quietly.
            args = (OtpPurpose.SIGNIN, _address()) if name == 'generate_code' else (_address(),)
            with pytest.raises(CodeSendThrottled):
                await getattr(service, name)(*args)

        assert len(consulted) == len(generators), consulted

    async def test_the_throttle_fails_open(self, monkeypatch):
        """A Redis blip must not lock everybody out of sign-in. Same
        trade the audit recorder makes in F90, for the same reason.

        Driven by breaking Redis rather than by grepping the module for
        the words "fails open" — which is what the first version did,
        and it failed on the capital F. Seventh time in this review a
        check has been beaten by the shape of text instead of behaviour,
        and here the behaviour is one monkeypatch away.
        """
        from app.services.auth.code_throttle import CodeThrottle

        async def _broken(self):
            raise ConnectionError('redis is gone')

        monkeypatch.setattr(CodeThrottle, '_redis', _broken)

        allowed = await CodeThrottle().allow(purpose='signin', email=_address(), code_pending=False)

        assert allowed is True, 'a Redis outage would stop every sign-in code'

    def test_the_log_line_masks_the_address(self):
        """It goes to logs F102 made shippable, and an operator needs the
        domain rather than the mailbox."""
        from app.services.auth.code_throttle import _mask

        assert _mask('jane.doe@example.com') == 'j***@example.com'
        assert 'jane.doe' not in _mask('jane.doe@example.com')

    def test_refusals_are_counted(self):
        """So sustained abuse is visible without reading logs. The alert
        rule for this metric is in charts/igni/files/alerts.yaml."""
        assert 'igni_email_codes_throttled_total' in (_ROOT / 'charts/igni/files/alerts.yaml').read_text()


class TestAResendDoesNotBecomeAWeapon:
    """The design the first version got wrong, and the tests that pin it.

    A silent refusal on cooldown broke "resend the code" — the commonest
    action on an OTP screen — and broke it *invisibly*, because the
    response is deliberately identical. Eleven existing OTP tests failed
    and were right to.

    The rule that came out of it has three parts, and each is a way to get
    it wrong:

    * within the cooldown, with a code pending: **send nothing**. It is a
      double-clicked button, and they already have a working code;
    * after the cooldown, with a code pending: **re-send the same code**,
      never a new one. Rotating it is what let an anonymous caller stop a
      real user from ever completing a sign-in;
    * with no code pending — consumed, or destroyed by five wrong
      guesses: **issue a fresh one immediately**. The cooldown must not
      apply there, or it lands on exactly the person it was meant to
      protect.
    """

    @staticmethod
    async def _expire_cooldown(purpose: str, email: str) -> None:
        """What waiting out the cooldown does, without waiting."""
        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            await client.delete(f'code_throttle:cool:{purpose}:{email.strip().lower()}')
        finally:
            await client.aclose()

    async def test_an_immediate_second_request_sends_nothing(self, client):
        from app.services.auth.otp_service import CodeSendThrottled, OTPService

        email = _address()
        await OTPService().generate_signin_code(email=email)

        with pytest.raises(CodeSendThrottled):
            await OTPService().generate_signin_code(email=email)

    async def test_a_resend_after_the_cooldown_returns_the_same_code(self, client):
        from app.services.auth.otp_service import OTPService

        email = _address()
        first = await OTPService().generate_signin_code(email=email)
        await self._expire_cooldown('signin', email)

        second = await OTPService().generate_signin_code(email=email)

        assert second.code == first.code, 'the resend rotated the code out from under the user'

    async def test_a_resend_does_not_reset_the_guessing_allowance(self, client):
        """The subtle one. A fresh code resets the allowance on purpose,
        so if a resend did too, an attacker could restore their guesses
        indefinitely by asking for one between bursts."""
        from app.services.auth.otp_service import OTPService

        email = _address()
        await OTPService().generate_signin_code(email=email)

        for _ in range(2):
            with pytest.raises(Exception):
                await OTPService().validate_and_signin_with_code(email, '000000')

        await self._expire_cooldown('signin', email)

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            before = await redis_client.get(f'otp:attempts:signin:{email}')
            await OTPService().generate_signin_code(email=email)
            after = await redis_client.get(f'otp:attempts:signin:{email}')
        finally:
            await redis_client.aclose()

        assert before == after == '2', (before, after)

    async def test_a_new_code_is_issued_once_the_old_one_is_gone(self, client):
        """The case the cooldown must not block. Five wrong guesses
        deletes the code on purpose — "the attacker has to make the real
        user request another one" — and that user must be able to,
        immediately, without waiting out a cooldown they did not cause.
        """
        from app.services.auth.otp_service import _MAX_ATTEMPTS, OTPService

        email = _address()
        _register(email)
        first = await OTPService().generate_signin_code(email=email)

        for _ in range(_MAX_ATTEMPTS):
            with pytest.raises(Exception):
                await OTPService().validate_and_signin_with_code(email, '000000')

        # No cooldown expiry: this is the point. The code is gone, so a
        # fresh one must be issued straight away.
        second = await OTPService().generate_signin_code(email=email)

        assert second.code != first.code, 'no new code was issued after the old one was destroyed'
        assert (await OTPService().validate_and_signin_with_code(email, second.code)).access_token
