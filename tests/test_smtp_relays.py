"""Sending mail through the three relays customers actually run.

They are not variations on a theme — each is a different server that
behaves differently on connect, and two of the three were unreachable
before:

* **Amazon SES** on port 587 (STARTTLS) or 465 (implicit TLS). The 465
  form was impossible: ``start_tls=True`` was hardcoded, and aiosmtplib
  raises when both TLS flags are set.
* **An internal relay** on port 25 with no TLS and no auth — the normal
  shape for an air-gapped install. Also impossible, for the same reason,
  and doubly so because empty credentials were still passed and
  aiosmtplib authenticates whenever ``username is not None``.
* **A hosted provider** on 587 with credentials, which did work.

The assertions are about what reaches ``aiosmtplib.send``, because that
is where the mistake was and mocking it is the only way to check the
handshake without a server.
"""

from email.message import EmailMessage

import pytest

from app.services.email.service import EmailService
from app.services.email.transports.smtp import SmtpTransport


@pytest.fixture
def sent(monkeypatch):
    """Capture the kwargs handed to aiosmtplib rather than dialling out."""
    calls: list[dict] = []

    async def _send(message, **kwargs):
        calls.append({'message': message, **kwargs})

    monkeypatch.setattr('app.services.email.transports.smtp.aiosmtplib.send', _send)
    return calls


async def _deliver(transport: SmtpTransport, **service_kwargs) -> None:
    await EmailService(transport=transport, **service_kwargs).send_email('dev@acme.test', 'Subject', '<p>Body</p>')


class TestAmazonSES:
    async def test_starttls_on_587(self, sent):
        """The documented default, and what most SES setups use."""
        await _deliver(
            SmtpTransport(
                host='email-smtp.eu-west-1.amazonaws.com',
                port=587,
                username='AKIAIOSFODNN7EXAMPLE',
                password='ses-smtp-password',
                tls_mode='starttls',
            )
        )
        call = sent[0]
        assert call['hostname'] == 'email-smtp.eu-west-1.amazonaws.com'
        assert call['port'] == 587
        assert call['start_tls'] is True
        assert call['use_tls'] is False
        assert call['username'] == 'AKIAIOSFODNN7EXAMPLE'

    async def test_implicit_tls_on_465(self, sent):
        """SES offers this too, and some corporate egress rules allow only
        465. It was unreachable while start_tls was hardcoded on."""
        await _deliver(
            SmtpTransport(
                host='email-smtp.eu-west-1.amazonaws.com',
                port=465,
                username='AKIAIOSFODNN7EXAMPLE',
                password='ses-smtp-password',
                tls_mode='tls',
            )
        )
        call = sent[0]
        assert call['use_tls'] is True
        assert call['start_tls'] is False

    async def test_the_two_tls_flags_are_never_both_set(self, sent):
        """aiosmtplib raises when they are, which would turn a
        misconfiguration into an unhandled error rather than a refusal."""
        for mode in ('starttls', 'tls', 'none'):
            sent.clear()
            await _deliver(SmtpTransport(host='relay.test', tls_mode=mode))
            assert not (sent[0]['start_tls'] and sent[0]['use_tls'])


class TestInternalRelay:
    """Postfix or Exchange on a private network: no TLS, no auth."""

    async def test_no_tls_is_possible(self, sent):
        await _deliver(SmtpTransport(host='mail.internal', port=25, tls_mode='none'))
        call = sent[0]
        assert call['start_tls'] is False
        assert call['use_tls'] is False

    async def test_no_credentials_means_no_auth_attempt(self, sent):
        """The bug this replaces. aiosmtplib authenticates whenever
        ``username is not None``, so passing an empty string made it try
        to AUTH against a server offering none — and the whole send
        failed."""
        await _deliver(SmtpTransport(host='mail.internal', port=25, username='', tls_mode='none'))
        assert sent[0]['username'] is None
        assert sent[0]['password'] is None

    async def test_credentials_are_passed_when_they_exist(self, sent):
        await _deliver(SmtpTransport(host='mail.internal', username='postfix', password='pw'))
        assert sent[0]['username'] == 'postfix'
        assert sent[0]['password'] == 'pw'


class TestFromHeader:
    async def test_the_display_name_is_configurable(self, sent):
        """A self-hosted deployment is the customer's. Their people should
        not get sign-in codes from a vendor's brand."""
        await _deliver(
            SmtpTransport(host='relay.test'),
            from_name='Acme Engineering',
            from_email='noreply@acme.test',
        )
        assert sent[0]['message']['From'] == 'Acme Engineering <noreply@acme.test>'

    async def test_the_address_is_what_ses_must_have_verified(self, sent):
        """SES rejects a From address that is not a verified identity, so
        it has to be exactly the configured one and not derived."""
        await _deliver(SmtpTransport(host='relay.test'), from_email='verified@acme.test')
        assert 'verified@acme.test' in sent[0]['message']['From']


class TestDelivery:
    async def test_a_message_carries_both_parts(self, sent):
        service = EmailService(transport=SmtpTransport(host='relay.test'))
        await service.send_email('dev@acme.test', 'Subject', '<p>HTML</p>', 'plain')
        message = sent[0]['message']
        assert isinstance(message, EmailMessage)
        assert message.is_multipart()

    async def test_a_relay_failure_is_reported_not_raised(self, monkeypatch):
        """Callers are background tasks; an exception there is invisible.
        A False return is at least checkable."""

        async def _boom(message, **kwargs):
            raise ConnectionRefusedError('no relay here')

        monkeypatch.setattr('app.services.email.transports.smtp.aiosmtplib.send', _boom)
        service = EmailService(transport=SmtpTransport(host='relay.test'))
        assert await service.send_email('a@b.test', 's', '<p>b</p>') is False
