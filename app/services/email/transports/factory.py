"""Pick the transport this deployment is configured for.

One function rather than a registry: there are two backends, the choice
is a single setting, and an unknown value should fail at startup with a
message naming the valid ones.
"""

from app.config import settings
from app.services.email.transports.base import EmailTransport
from app.services.email.transports.ses import SesTransport
from app.services.email.transports.smtp import SmtpTransport


class UnknownEmailBackend(ValueError):
    """``email_backend`` names something that does not exist."""


def build_transport(backend: str | None = None) -> EmailTransport:
    """The configured transport.

    Raised rather than defaulted on an unknown name: silently falling
    back to SMTP would mean a customer who typed ``sesv2`` got mail from
    a relay they never configured, or no mail at all, with nothing saying
    why.
    """
    chosen = backend if backend is not None else settings.email_backend
    if chosen == 'smtp':
        return SmtpTransport()
    if chosen == 'ses':
        return SesTransport()
    raise UnknownEmailBackend(f'email_backend must be "smtp" or "ses", got {chosen!r}')
