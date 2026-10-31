"""Rendering and sending igni's outbound mail.

Templates live here; getting the bytes to a provider is a transport's
job (``app/services/email/transports``). The split exists because
Amazon SES can be reached two entirely different ways — SMTP with a
stored username and password, or its own API with the AWS credential
chain — and that choice is about credential management rather than
about email. Every message renders identically either way.

Whether email can be sent *at all* is a separate question again, and
``EmailDelivery`` owns it: a self-hosted deployment may have no relay,
which is supported rather than broken.
"""

from email.message import EmailMessage
import logging

from app.config import settings
from app.services.email.templates import (
    get_otp_email_template,
    get_password_reset_email_template,
    get_sign_up_email_template,
    get_team_invite_email_template,
    get_welcome_email_template,
)
from app.services.email.transports.base import EmailTransport
from app.services.email.transports.factory import build_transport

logger = logging.getLogger(__name__)


class EmailService:
    """Renders a message and hands it to the configured transport."""

    def __init__(
        self,
        transport: EmailTransport | None = None,
        from_email: str | None = None,
        from_name: str | None = None,
    ):
        # Built lazily so constructing the service never touches AWS or
        # SMTP configuration — several call sites instantiate it just to
        # reach one template method.
        self._transport = transport
        self.from_email = from_email if from_email is not None else settings.email_from_address
        self.from_name = from_name if from_name is not None else settings.email_from_name

    @property
    def transport(self) -> EmailTransport:
        if self._transport is None:
            self._transport = build_transport()
        return self._transport

    async def send_email(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: str | None = None,
    ) -> bool:
        """Render and send. Returns True on success, never raises.

        "Never raises" was not true, and B7 found it from the other end:
        `EmailMessage.__setitem__` refuses a header value containing CR
        or LF — which is the stdlib holding the header-injection boundary
        for us, and worth keeping — but it does so with a `ValueError`
        that came straight out of here. On the internal notification path
        the address is supplied by the caller as a bare `str`, so a
        header-injection *attempt* became an exception in whatever was
        sending, and the one call site that swallows everything
        (`notifications._send`) was hiding it.

        So the assembly is guarded and the refusal is logged as what it
        is. Not sanitised: a stripped CR would send the mail somewhere,
        and an address with a newline in it is not a typo.
        """
        env = settings.app_env.lower()
        if env != 'prod':
            subject = f'[{env.upper()}] {subject}'

        try:
            message = EmailMessage()
            # The address must be exactly what the provider expects: SES
            # rejects a From that is not a verified identity in the account.
            message['From'] = f'{self.from_name} <{self.from_email}>'
            message['To'] = to_email
            message['Subject'] = subject

            if text_body:
                message.set_content(text_body)
                message.add_alternative(html_body, subtype='html')
            else:
                message.set_content(html_body, subtype='html')
        except ValueError:
            # Deliberately without the value: it is an address, and this
            # log is not the place for one (LG-5).
            logger.warning('Refusing to send: the recipient or subject contains a line break')
            return False

        return await self.transport.send(message)

    # -- Auth emails ----------------------------------------------------------

    async def send_otp_code(self, to_email: str, code: str, name: str | None = None) -> bool:
        content = get_otp_email_template(code=code, email=to_email, name=name)
        text_body = f'Your sign-in code is: {code}\nThis code will expire in 10 minutes.'
        return await self.send_email(to_email, content.subject, content.html_body, text_body)

    async def send_sign_up_code(self, to_email: str, code: str, name: str | None = None) -> bool:
        content = get_sign_up_email_template(code=code, email=to_email, name=name)
        text_body = f'Your verification code is: {code}\nThis code will expire in 10 minutes.'
        return await self.send_email(to_email, content.subject, content.html_body, text_body)

    async def send_welcome(self, to_email: str, name: str | None = None) -> bool:
        content = get_welcome_email_template(email=to_email, name=name)
        text_body = 'Welcome to ignite-ember.sh! Your account has been created successfully.'
        return await self.send_email(to_email, content.subject, content.html_body, text_body)

    async def send_team_invite(
        self, to_email: str, role: str, invited_by: str | None = None, name: str | None = None
    ) -> bool:
        content = get_team_invite_email_template(email=to_email, role=role, invited_by=invited_by, name=name)
        inviter = f' by {invited_by}' if invited_by else ''
        signup_url = f'{settings.portal_base_url}/signup?email={to_email}'
        text_body = (
            f'You have been invited{inviter} to join igni as a {role.capitalize()}.\n'
            f'Accept the invitation: {signup_url}'
        )
        return await self.send_email(to_email, content.subject, content.html_body, text_body)

    async def send_password_reset_code(self, to_email: str, code: str, name: str | None = None) -> bool:
        content = get_password_reset_email_template(code=code, email=to_email, name=name)
        text_body = f'Your password reset code is: {code}\nThis code will expire in 10 minutes.'
        return await self.send_email(to_email, content.subject, content.html_body, text_body)
