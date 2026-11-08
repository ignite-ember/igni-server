"""Sending through the Amazon SES API.

The reason this transport exists is credentials rather than email. SES
over SMTP works fine and needs a username and password living in
configuration forever; the API resolves an instance profile, task role or
pod identity, so the deployment stores no mail secret at all. That is
what most of this file checks — that we do not accidentally pin static
credentials and defeat the point.

The rest is about the two ways SES refuses a message, because both are
fixed in the AWS console rather than in this deployment, and an operator
who cannot tell them apart from the log has to guess.
"""

from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default as default_policy

import pytest

from app.services.email.service import EmailService
from app.services.email.transports.factory import build_transport, UnknownEmailBackend
from app.services.email.transports.ses import SesTransport
from app.services.email.transports.smtp import SmtpTransport


class FakeSesClient:
    """Records what would have been sent, and can refuse like SES does."""

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.requests: list[dict] = []

    def send_email(self, **request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return {'MessageId': '0100018f-message-id'}


class ClientError(Exception):
    """Stands in for botocore's, which carries the code we log."""

    def __init__(self, code: str, message: str):
        self.response = {'Error': {'Code': code, 'Message': message}}
        super().__init__(f'An error occurred ({code}) ... {message}')


@pytest.fixture
def ses(monkeypatch):
    """A transport whose boto3 client is a recorder."""

    def _build(transport: SesTransport, error: Exception | None = None) -> FakeSesClient:
        client = FakeSesClient(error)
        monkeypatch.setattr(transport, '_client', lambda: client)
        return client

    return _build


def _parts(message: EmailMessage) -> list[tuple[str, str]]:
    return [(part.get_content_type(), part.get_content().strip()) for part in message.walk() if not part.is_multipart()]


async def _deliver(transport: SesTransport, **service_kwargs) -> bool:
    service = EmailService(transport=transport, **service_kwargs)
    return await service.send_email(
        to_email='dev@acme.test', subject='Subject', html_body='<p>Body</p>', text_body='plain'
    )


class TestCredentials:
    """The whole reason for this transport."""

    def test_no_static_keys_are_passed_when_none_are_configured(self, monkeypatch):
        """So boto3 falls through to the credential chain — environment,
        shared config, container endpoint, IMDS — and an EC2/ECS/EKS
        deployment authenticates with its role."""
        captured: dict = {}

        def _fake_client(service, **kwargs):
            captured.update(kwargs)
            return FakeSesClient()

        monkeypatch.setattr('app.services.email.transports.ses.boto3.client', _fake_client)
        SesTransport(region='eu-west-1', access_key_id='')._client()

        assert 'aws_access_key_id' not in captured
        assert 'aws_secret_access_key' not in captured

    def test_static_keys_are_used_when_given(self, monkeypatch):
        """Not every deployment has a role available."""
        captured: dict = {}

        def _fake_client(service, **kwargs):
            captured.update(kwargs)
            return FakeSesClient()

        monkeypatch.setattr('app.services.email.transports.ses.boto3.client', _fake_client)
        SesTransport(access_key_id='AKIA...', secret_access_key='secret')._client()

        assert captured['aws_access_key_id'] == 'AKIA...'

    def test_the_description_says_which_is_in_use(self):
        """An operator reading a startup log needs to know whether to go
        looking at a role or at a secret."""
        assert 'ambient AWS credential chain' in SesTransport(region='eu-west-1', access_key_id='').description
        assert 'static credentials' in SesTransport(access_key_id='AKIA...').description


class TestSending:
    async def test_a_message_is_sent_raw(self, ses):
        """Not composed by SES from subject and body strings: the message
        is already multipart with the headers we intend, and sending the
        rendered bytes means both transports emit identical mail."""
        transport = SesTransport(region='eu-west-1')
        client = ses(transport)

        assert await _deliver(transport) is True

        raw = client.requests[0]['Content']['Raw']['Data']
        assert b'Subject' in raw
        assert b'dev@acme.test' in raw

    async def test_the_envelope_is_not_duplicated(self, ses):
        """SES reads From and To out of the raw message. Passing them
        again invites the two disagreeing."""
        transport = SesTransport()
        client = ses(transport)
        await _deliver(transport)

        assert 'Destination' not in client.requests[0]
        assert 'FromEmailAddress' not in client.requests[0]

    async def test_a_configuration_set_is_passed_when_configured(self, ses):
        """How a customer wires up bounce and complaint handling."""
        transport = SesTransport(configuration_set='igni-events')
        client = ses(transport)
        await _deliver(transport)

        assert client.requests[0]['ConfigurationSetName'] == 'igni-events'

    async def test_it_is_omitted_when_not(self, ses):
        """SES rejects an empty configuration set name rather than
        ignoring it."""
        transport = SesTransport(configuration_set='')
        client = ses(transport)
        await _deliver(transport)

        assert 'ConfigurationSetName' not in client.requests[0]

    async def test_both_body_parts_survive(self, ses):
        transport = SesTransport()
        client = ses(transport)
        await _deliver(transport)

        raw = client.requests[0]['Content']['Raw']['Data']
        assert b'multipart/alternative' in raw


class TestRefusals:
    """The two ways SES says no, both fixed in the AWS console."""

    async def test_an_unverified_sender_is_reported_not_raised(self, ses, caplog):
        transport = SesTransport()
        ses(transport, ClientError('MessageRejected', 'Email address is not verified'))

        assert await _deliver(transport) is False
        assert 'MessageRejected' in caplog.text

    async def test_a_sandboxed_account_names_the_reason(self, ses, caplog):
        """A new SES account can only send to verified addresses. The code
        is the difference between "verify the sender" and "request
        production access"."""
        transport = SesTransport()
        ses(transport, ClientError('AccountSendingPausedException', 'Sending is paused'))

        assert await _deliver(transport) is False
        assert 'AccountSendingPausedException' in caplog.text

    async def test_an_unexpected_failure_is_still_survivable(self, ses):
        """Callers are background tasks, where an exception is invisible."""
        transport = SesTransport()
        ses(transport, RuntimeError('the network went away'))

        assert await _deliver(transport) is False


class TestBackendSelection:
    def test_smtp_is_the_default(self):
        assert isinstance(build_transport('smtp'), SmtpTransport)

    def test_ses_is_selectable(self):
        assert isinstance(build_transport('ses'), SesTransport)

    def test_an_unknown_backend_is_refused_rather_than_defaulted(self):
        """Falling back to SMTP would mean a customer who typed 'sesv2'
        got mail from a relay they never configured, or none at all, with
        nothing saying why."""
        with pytest.raises(UnknownEmailBackend, match='sesv2'):
            build_transport('sesv2')


class TestParity:
    async def test_both_transports_render_the_same_message(self, ses, monkeypatch):
        """A customer moving from SMTP to the API should not get subtly
        different mail."""
        smtp_messages: list[EmailMessage] = []

        async def _smtp_send(message, **kwargs):
            smtp_messages.append(message)

        monkeypatch.setattr('app.services.email.transports.smtp.aiosmtplib.send', _smtp_send)
        await EmailService(transport=SmtpTransport(host='relay.test')).send_email(
            to_email='dev@acme.test', subject='Subject', html_body='<p>Body</p>', text_body='plain'
        )

        transport = SesTransport()
        client = ses(transport)
        await _deliver(transport)

        # Compared part by part rather than byte for byte: MIME boundaries
        # are randomly generated per message, so identical mail never has
        # identical bytes.
        # ``policy=default`` so the parsed message is an EmailMessage with
        # the modern API, rather than the legacy compat class.
        ses_message = message_from_bytes(client.requests[0]['Content']['Raw']['Data'], policy=default_policy)
        smtp_message = smtp_messages[0]

        for header in ('From', 'To', 'Subject'):
            assert smtp_message[header] == ses_message[header]
        assert smtp_message.get_content_type() == ses_message.get_content_type()
        assert _parts(smtp_message) == _parts(ses_message)
