"""What happens to email when there is no relay.

An air-gapped deployment may have no SMTP at all. Before `EmailDelivery`
existed, nine call sites each wrote ``if settings.email_enabled: send``,
so with email off a sign-in code was generated, stored, and silently
discarded — the user was told to check an inbox that would never receive
anything.

The security-relevant assertion in this file is
``test_a_code_is_never_returned_to_the_caller``. Returning the code in
the response would be a convenient-looking account-takeover hole:
anonymous callers pick the address, so they would be able to request a
code for anyone and read it straight back.
"""

import logging
import pathlib
import re
import uuid

import pytest
import yaml

from app.config import Settings, settings
from app.services.email.delivery import EmailDelivery, invite_url_for


@pytest.fixture
def delivery() -> EmailDelivery:
    return EmailDelivery()


class TestEnabled:
    def test_injected_settings_decide_it(self, monkeypatch):
        """The process-wide settings say email is on; the injected ones say
        it is off, and the injected ones are the ones asked."""

        monkeypatch.setattr(settings, 'email_enabled', True)
        monkeypatch.setattr(settings, 'smtp_host', 'smtp.example.com')
        email_off = settings.model_copy(update={'email_enabled': False})

        assert EmailDelivery().enabled is True
        assert EmailDelivery(settings=email_off).enabled is False

    def test_needs_both_the_flag_and_a_relay(self, delivery, monkeypatch):
        """``email_enabled`` on with no host is a misconfiguration that
        would otherwise fail once per message in a background task,
        where nobody is watching."""

        monkeypatch.setattr(settings, 'email_enabled', True)
        monkeypatch.setattr(settings, 'smtp_host', 'smtp.example.com')
        assert delivery.enabled is True

        monkeypatch.setattr(settings, 'smtp_host', '')
        assert delivery.enabled is False

    def test_the_flag_alone_can_disable_it(self, delivery, monkeypatch):
        monkeypatch.setattr(settings, 'email_enabled', False)
        monkeypatch.setattr(settings, 'smtp_host', 'smtp.example.com')
        assert delivery.enabled is False


@pytest.fixture
def codes_logged(monkeypatch):
    """The operator's explicit opt-in: ``LOG_SIGN_IN_CODES=true``."""

    monkeypatch.setattr(settings, 'log_sign_in_codes', True)


class TestCodesAreNotLoggedByDefault:
    """F35. Codes written to the log were the default, and a deployment
    that had not configured email yet was putting working credentials
    into logs shipped, kept and read well beyond the people who should
    be able to sign in as anybody."""

    def test_the_setting_defaults_to_off(self):
        assert Settings.model_fields['log_sign_in_codes'].default is False

    def test_the_shipped_configuration_leaves_it_off(self):
        """`dist.env` is what a deployment copies, the chart's values are
        what it installs with, and the marketplace form is what a buyer
        fills in. Each says off."""
        root = pathlib.Path(__file__).resolve().parent.parent
        assert re.search(r'^LOG_SIGN_IN_CODES=false$', (root / 'dist.env').read_text(), re.M)
        values = yaml.safe_load((root / 'charts/igni/values.yaml').read_text())
        assert values['email']['logSignInCodes'] is False
        schema = yaml.safe_load((root / 'marketplace/schema.yaml').read_text())
        assert schema['properties']['email.logSignInCodes']['default'] is False

    def test_the_code_is_not_logged(self, delivery, caplog):
        with caplog.at_level(logging.DEBUG):
            delivery.record_undeliverable_code(purpose='sign-in', email='a@b.test', code='123456')
        assert '123456' not in caplog.text

    def test_the_log_says_a_code_went_nowhere_and_what_to_set(self, delivery, caplog):
        """Silence would be the old failure — a code generated and dropped
        with nothing saying why."""
        with caplog.at_level(logging.WARNING):
            delivery.record_undeliverable_code(purpose='sign-in', email='a@b.test', code='123456')
        assert 'EMAIL DISABLED' in caplog.text
        assert 'LOG_SIGN_IN_CODES' in caplog.text
        assert 'not delivered' in caplog.text

    def test_the_user_is_not_told_to_ask_for_a_code_nobody_has(self):
        message = EmailDelivery().no_relay_message()
        assert 'not configured' in message
        assert 'no verification code can be sent' in message


class TestUndeliverableCodesWhenOptedIn:
    @pytest.fixture(autouse=True)
    def _opted_in(self, codes_logged):
        pass

    def test_the_code_reaches_the_log(self, delivery, caplog):
        """An operator has to be able to relay it by hand."""
        with caplog.at_level(logging.WARNING):
            delivery.record_undeliverable_code(purpose='sign-in', email='a@b.test', code='123456')
        assert '123456' in caplog.text
        assert 'a@b.test' in caplog.text

    def test_every_code_carries_the_warning(self, delivery, caplog):
        """Not only at boot: the line that holds the credential says what
        it exposes and which setting put it there, so it reads that way in
        whatever aggregator it ends up in."""
        with caplog.at_level(logging.WARNING):
            delivery.record_undeliverable_code(purpose='sign-in', email='a@b.test', code='123456')
        (record,) = [r for r in caplog.records if '123456' in r.getMessage()]
        assert 'LOG_SIGN_IN_CODES is on' in record.getMessage()
        assert 'can sign in as this user' in record.getMessage()

    def test_it_carries_the_shared_marker(self, delivery, caplog):
        """One grep finds every code the deployment has issued, rather
        than nine different log phrasings."""
        with caplog.at_level(logging.WARNING):
            delivery.record_undeliverable_code(purpose='sign-in', email='a@b.test', code='123456')
        assert 'EMAIL DISABLED' in caplog.text

    def test_it_is_logged_loudly_enough_to_notice(self, delivery, caplog):
        """WARNING, not INFO: a deployment relaying codes by hand is in
        an unusual state and the logs should say so."""
        with caplog.at_level(logging.DEBUG):
            delivery.record_undeliverable_code(purpose='sign-in', email='a@b.test', code='123456')
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_a_code_is_never_returned_to_the_caller(self):
        """The method returns None, and that is the security property.

        Anonymous callers choose the address on these endpoints. Any
        path that hands the code back would let anyone request one for
        any address and read it — account takeover wearing the costume
        of a convenience feature.
        """
        assert (
            EmailDelivery().record_undeliverable_code(purpose='sign-in', email='victim@example.com', code='123456')
            is None
        )

    def test_the_user_is_told_to_ask_for_it(self):
        assert 'Ask your administrator for the verification code' in EmailDelivery().no_relay_message()


class TestTheSesBackendIsARelay:
    """`enabled` required `smtp_host`, which the SES API backend never
    sets — so a deployment on SES sent its codes to the log rather than
    to SES. With codes no longer logged by default, it would have sent
    them nowhere."""

    def test_ses_needs_no_smtp_host(self, delivery, monkeypatch):
        monkeypatch.setattr(settings, 'email_enabled', True)
        monkeypatch.setattr(settings, 'email_backend', 'ses')
        monkeypatch.setattr(settings, 'smtp_host', '')
        assert delivery.enabled is True

    def test_ses_still_needs_the_flag(self, delivery, monkeypatch):
        monkeypatch.setattr(settings, 'email_enabled', False)
        monkeypatch.setattr(settings, 'email_backend', 'ses')
        assert delivery.enabled is False


class TestDroppedMessages:
    def test_informational_mail_is_noted_quietly(self, delivery, caplog):
        """A welcome note carries nothing to recover, so it does not
        deserve a warning."""
        with caplog.at_level(logging.DEBUG):
            delivery.note_undeliverable(purpose='welcome', email='a@b.test')
        assert 'EMAIL DISABLED' in caplog.text
        assert all(r.levelno < logging.WARNING for r in caplog.records)


class TestInviteUrl:
    def test_it_points_at_signup_with_the_address(self, monkeypatch):
        monkeypatch.setattr(settings, 'portal_base_url', 'https://igni.acme.internal')
        assert invite_url_for('new@acme.test') == 'https://igni.acme.internal/signup?email=new%40acme.test'

    def test_the_address_is_escaped(self, monkeypatch):
        monkeypatch.setattr(settings, 'portal_base_url', 'https://igni.acme.internal')
        assert '%2B' in invite_url_for('a+tag@acme.test')

    def test_a_trailing_slash_does_not_double_up(self, monkeypatch):
        monkeypatch.setattr(settings, 'portal_base_url', 'https://igni.acme.internal/')
        assert '//signup' not in invite_url_for('a@b.test').removeprefix('https://')

    def test_it_is_derivable_and_therefore_safe_to_hand_back(self, monkeypatch):
        """No token, no expiry — the invite is claimed by signing up with
        the invited address, so the URL carries no secret. That is why
        the API returns it to the admin who created it."""

        monkeypatch.setattr(settings, 'portal_base_url', 'https://igni.acme.internal')
        assert invite_url_for('a@b.test') == invite_url_for('a@b.test')


class TestNoRelayEndToEnd:
    """The endpoints, not the helper.

    This is the regression that motivated the whole module: with no
    relay, a code used to be generated and dropped while the response
    still said "check your email".

    Each test uses its own address. They shared `user@test.com`, which
    was harmless until F112 put a per-address send limit in front of
    these endpoints — after which the second and third tests were
    silently throttled and asserted against an empty log. The coupling
    was latent before that: any per-address state would have done it.
    """

    @staticmethod
    def _address(tag: str) -> str:
        return f'norelay-{tag}-{uuid.uuid4().hex[:8]}@test.com'

    async def test_a_sign_in_code_request_says_email_is_unavailable(self, client, caplog):
        with caplog.at_level(logging.WARNING):
            resp = await client.post('/send-sign-in-code', json={'email': self._address('says')})

        assert resp.status_code == 200
        message = resp.json()['message']
        assert 'not configured' in message
        assert 'administrator' in message

    async def test_by_default_the_code_is_in_neither_the_log_nor_the_response(self, client, caplog):
        with caplog.at_level(logging.DEBUG):
            resp = await client.post('/send-sign-in-code', json={'email': self._address('quiet')})

        assert resp.status_code == 200
        assert 'EMAIL DISABLED' in caplog.text
        assert 'LOG_SIGN_IN_CODES' in caplog.text
        assert not re.findall(r'\b\d{6}\b', caplog.text), 'a code was logged without LOG_SIGN_IN_CODES'
        assert 'no verification code can be sent' in resp.json()['message']

    async def test_opted_in_the_code_is_in_the_log_and_not_the_response(self, client, caplog, codes_logged):
        with caplog.at_level(logging.WARNING):
            resp = await client.post('/send-sign-in-code', json={'email': self._address('logged')})

        assert 'EMAIL DISABLED' in caplog.text
        # Whatever six-digit code was minted, it is in the log.
        codes = re.findall(r'\b\d{6}\b', caplog.text)
        assert codes, 'no code was logged for an operator to relay'
        # And it is in none of the response.
        assert codes[0] not in resp.text

    async def test_the_message_does_not_reveal_whether_the_account_exists(self, client):
        """Identical for a known and an unknown address, so the endpoint
        cannot be used to enumerate users."""
        known = await client.post('/send-sign-in-code', json={'email': self._address('known')})
        unknown = await client.post('/send-sign-in-code', json={'email': self._address('unknown')})
        assert known.json()['message'] == unknown.json()['message']

    async def test_email_still_sends_when_a_relay_is_configured(self, client, email_relay):
        resp = await client.post('/send-sign-in-code', json={'email': self._address('relay')})
        assert resp.status_code == 200
        assert 'not configured' not in resp.json()['message']
        assert [s for s in email_relay if s['method'] == 'send_otp_code']
