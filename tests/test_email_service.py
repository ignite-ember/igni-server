"""EmailService — rendering, and the environment prefix.

Transport behaviour moved out to ``test_smtp_relays`` and
``test_ses_transport`` when SES gained its own backend; what is left here
is what the service itself decides before handing a message over.

These still exercise the SMTP transport rather than a stub, because the
subject prefix and the multipart assembly are only observable in the
message that reaches it.
"""

from unittest.mock import AsyncMock, patch

from app.services.email.service import EmailService
from app.services.email.transports.smtp import SmtpTransport


class TestEmailService:
    async def test_send_email_success(self, monkeypatch):
        with patch('app.services.email.transports.smtp.aiosmtplib.send', new_callable=AsyncMock) as mock_send:
            transport = SmtpTransport(host='relay.test', port=2525)
            result = await EmailService(transport=transport).send_email(
                'recipient@test.com', 'Test Subject', '<p>Hello</p>', 'Hello'
            )

        assert result is True
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args[1]
        assert call_kwargs['hostname'] == 'relay.test'
        assert call_kwargs['port'] == 2525

    async def test_send_email_auth_error(self, monkeypatch):
        import aiosmtplib

        with patch(
            'app.services.email.transports.smtp.aiosmtplib.send',
            new_callable=AsyncMock,
            side_effect=aiosmtplib.SMTPAuthenticationError(535, 'Authentication failed'),
        ):
            svc = EmailService(transport=SmtpTransport(host='relay.test'))
            result = await svc.send_email('recipient@test.com', 'Subject', '<p>body</p>')

        assert result is False

    async def test_subject_prefix_non_prod(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, 'app_env', 'stage')

        with patch('app.services.email.transports.smtp.aiosmtplib.send', new_callable=AsyncMock) as mock_send:
            svc = EmailService(transport=SmtpTransport(host='relay.test'))
            await svc.send_email('to@test.com', 'My Subject', '<p>hi</p>')

        msg = mock_send.call_args[0][0]
        assert msg['Subject'].startswith('[STAGE]')
        assert 'My Subject' in msg['Subject']

    async def test_no_prefix_prod(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, 'app_env', 'prod')

        with patch('app.services.email.transports.smtp.aiosmtplib.send', new_callable=AsyncMock) as mock_send:
            svc = EmailService(transport=SmtpTransport(host='relay.test'))
            await svc.send_email('to@test.com', 'My Subject', '<p>hi</p>')

        msg = mock_send.call_args[0][0]
        assert msg['Subject'] == 'My Subject'
        assert not msg['Subject'].startswith('[')
