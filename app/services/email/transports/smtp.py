"""Mail over SMTP, against whatever relay the customer runs.

Three shapes in practice, and they are different servers rather than
preferences — see ``smtp_tls_mode`` in the settings for why this cannot
be two booleans:

* **Amazon SES** — ``email-smtp.<region>.amazonaws.com``, port 587
  (STARTTLS) or 465 (implicit TLS). Credentials are SES *SMTP*
  credentials, derived from an IAM user and not the IAM access key
  itself. Prefer ``SesTransport`` where you can: it needs no stored
  secret at all.
* **A hosted provider** — Google Workspace, Postmark, Mailgun. Port 587,
  STARTTLS, username and password.
* **An internal relay** — Postfix or Exchange on a private network, port
  25, frequently with no TLS and no authentication. The common shape for
  an air-gapped install, and the one that used to fail outright.
"""

from __future__ import annotations

from email.message import EmailMessage
import logging

import aiosmtplib

from app.config import settings
from app.services.email.transports.base import EmailTransport

logger = logging.getLogger(__name__)

# ``(start_tls, use_tls)`` per mode. aiosmtplib treats the two as
# mutually exclusive and raises when both are set, so they are spelled
# out together rather than derived from independent flags that could
# contradict each other.
_TLS_FLAGS: dict[str, tuple[bool, bool]] = {
    'starttls': (True, False),
    'tls': (False, True),
    'none': (False, False),
}


class SmtpTransport(EmailTransport):
    """Sends through the configured SMTP relay."""

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        tls_mode: str | None = None,
    ) -> None:
        # ``is not None`` rather than ``or``: an explicit empty username
        # means "this relay takes no credentials", and ``or`` would read
        # that as "not supplied" and fall back to the configured one.
        self.host = host if host is not None else settings.smtp_host
        self.port = port if port is not None else settings.smtp_port
        self.username = username if username is not None else settings.smtp_username
        self.password = password if password is not None else settings.smtp_password
        self.tls_mode = tls_mode if tls_mode is not None else settings.smtp_tls_mode

    @property
    def description(self) -> str:
        return f'SMTP to {self.host}:{self.port} ({self.tls_mode})'

    async def send(self, message: EmailMessage) -> bool:
        start_tls, use_tls = _TLS_FLAGS[self.tls_mode]
        username, password = self._credentials()
        try:
            await aiosmtplib.send(
                message,
                hostname=self.host,
                port=self.port,
                username=username,
                password=password,
                start_tls=start_tls,
                use_tls=use_tls,
            )
        except aiosmtplib.SMTPAuthenticationError as exc:
            logger.error('SMTP authentication failed against %s: %s', self.host, exc)
            return False
        except aiosmtplib.SMTPException as exc:
            logger.error('SMTP error sending to %s: %s', message['To'], exc)
            return False
        except Exception as exc:  # noqa: BLE001 — a send must not escape into a background task
            logger.error('Unexpected error sending to %s: %s', message['To'], exc)
            return False

        logger.info('Email sent via SMTP to %s', message['To'])
        return True

    def _credentials(self) -> tuple[str | None, str | None]:
        """Credentials, or ``None`` when none are configured.

        ``None`` rather than an empty string. aiosmtplib decides whether
        to authenticate with ``if username is not None``, so ``''`` is
        still an attempt — and an internal relay offering no AUTH rejects
        it, taking email down on exactly the deployments least equipped
        to debug it.
        """
        if not self.username:
            return None, None
        return self.username, self.password
