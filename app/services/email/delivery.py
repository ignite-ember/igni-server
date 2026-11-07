"""What happens to an email when there is nowhere to send it.

An air-gapped deployment may have no SMTP relay at all, and igni has to
work anyway. Before this existed, nine call sites each wrote
``if settings.email_enabled: send(...)`` — which meant that with email
off, a sign-in code was generated, stored, and then silently discarded.
The user was told to check an inbox that would never receive anything,
and nothing in the logs said why.

That is the failure this module exists to remove. Email being
unavailable is a supported configuration, so every flow that depends on
it needs a defined answer for "and if it cannot be sent?".

## The two answers, and why they differ

**Codes are never returned, and logged only when the operator says so.**
A sign-in or password-reset code is requested by an anonymous caller who
has typed in an email address. Returning the code in the response would
let anyone request one for any address and read it straight back —
account takeover, dressed as a convenience feature.

The log is the alternative, and it used to be the default. It is not any
more (F35): logs are shipped to an aggregator, kept for months and read
by more people than should be able to sign in as anybody, and a
deployment that simply had not configured email yet was writing working
credentials into them without having chosen to. So a code is written to
the log only with ``LOG_SIGN_IN_CODES=true`` — the documented way in for
first setup on a deployment with no relay — and every code written says,
in the same line, that the setting is on and what it exposes. With it
off, the log records that a code was issued and not delivered, and what
to configure; never the code.

**Invite links are returned to the admin who made them.** Different
situation entirely — the caller is authenticated, already an admin of
that organization, and already authorised to grant exactly this access.
Handing the link back so they can pass it along themselves is safe, and
it is the difference between "invites work without email" and "invites
are broken without email".
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from app.config import settings

logger = logging.getLogger(__name__)

# A single grep-able marker. An operator supporting a no-email
# deployment should be able to find every code the system has issued
# with one search, rather than knowing nine different log phrasings.
_MARKER = 'EMAIL DISABLED'


class EmailDelivery:
    """Decides whether email can be sent, and what to do when it cannot."""

    @property
    def enabled(self) -> bool:
        """Whether outbound email is configured for this deployment.

        Both a flag and a transport are required: ``email_enabled`` on
        with no ``smtp_host`` is a misconfiguration that would otherwise
        fail once per message in a background task, where nobody is
        watching. The SES API backend needs no host — it resolves its
        region and credentials itself — so it counts on the flag alone.
        It used to need ``smtp_host`` too, which sent every SES
        deployment's codes to the log instead of to SES.
        """
        if not settings.email_enabled:
            return False
        return settings.email_backend == 'ses' or bool(settings.smtp_host)

    @property
    def logs_codes(self) -> bool:
        """Whether an undeliverable code is written to the log (``LOG_SIGN_IN_CODES``)."""
        return settings.log_sign_in_codes

    def record_undeliverable_code(self, *, purpose: str, email: str, code: str) -> None:
        """Account for a one-time code that could not be emailed.

        Logged only with ``LOG_SIGN_IN_CODES`` on; otherwise the log says a
        code was issued and how to make codes reachable, without the
        code. Never returned to the caller — see the module docstring for
        why that would be an account-takeover hole rather than a
        convenience.
        """
        if not self.logs_codes:
            logger.warning(
                '%s — a %s code for %s was issued and not delivered: no relay is configured '
                'and LOG_SIGN_IN_CODES is off, so it was not logged either. Configure a relay '
                '(EMAIL_ENABLED and SMTP_HOST), or for first setup set LOG_SIGN_IN_CODES=true.',
                _MARKER,
                purpose,
                email,
            )
            return
        logger.warning(
            '%s — %s code for %s is %s. No relay is configured and LOG_SIGN_IN_CODES is on, '
            'so it was written here instead: anyone who can read this log can sign in as this '
            'user. Give it to the user through a channel you trust, and turn LOG_SIGN_IN_CODES '
            'off once a relay is configured.',
            _MARKER,
            purpose,
            email,
            code,
        )

    @staticmethod
    def no_relay_message() -> str:
        """What the person asking for a code is told when there is no relay.

        Deliberately vague about *where* a code went: it does not name the
        log, and it is the same for every address, so it cannot be used to
        enumerate users. It differs only by the deployment-wide setting,
        because "ask your administrator for the code" is untrue when no
        administrator has it.
        """
        if settings.log_sign_in_codes:
            return (
                'Email delivery is not configured on this deployment. '
                'Ask your administrator for the verification code.'
            )
        return (
            'Email delivery is not configured on this deployment, so no verification code can be sent. '
            'Ask your administrator to configure it.'
        )

    def note_undeliverable(self, *, purpose: str, email: str) -> None:
        """Log a message that was dropped and carries nothing to recover.

        Welcome notes and other notices, where the content is
        informational and losing it costs nothing that has to be
        replaced by hand.
        """
        logger.info('%s — skipped the %s message to %s', _MARKER, purpose, email)


def invite_url_for(email: str) -> str:
    """Where an invited person goes to claim their membership.

    Not a secret, and not a token: the invite is claimed by signing up
    with the invited address, so this URL is derivable from the address
    alone. That is why it is safe to hand back to the admin who created
    it, and why it does not expire.
    """
    return f'{settings.portal_base_url.rstrip("/")}/signup?email={quote(email)}'
