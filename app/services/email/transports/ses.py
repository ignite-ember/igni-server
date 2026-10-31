"""Amazon SES over its own API, authenticated by the AWS credential chain.

The reason to prefer this over SES-via-SMTP is credentials, not email.
SMTP needs a username and password that live in configuration forever;
the API uses whatever the AWS SDK resolves — and on EC2, ECS or EKS that
is the instance profile, task role or pod identity. A deployment then
stores **no mail secret at all**, which is the difference between a
rotatable role and a password in a Helm values file. It also matters for
a Marketplace listing, where "bring your own IAM role" is expected and
"paste an SMTP password" is a smell.

Credentials are deliberately never passed explicitly here unless the
customer sets them. ``boto3.client`` with no keys walks the standard
chain: environment, shared config file, container credentials endpoint,
then IMDS. Each of those is a supported way to run, including
``AWS_PROFILE`` with a ``role_arn``, which is how cross-account
assume-role works without a line of code from us.

## What SES will refuse

* A ``From`` address that is not a verified identity in this account.
* Any recipient at all, if the account is still in the sandbox — new
  accounts can only send to verified addresses until AWS grants
  production access.

Both surface as ``ClientError`` with a name worth logging verbatim,
because the fix is in the AWS console rather than in this deployment.

## Raw, not composed

SESv2 will assemble a message from subject and body strings, and we do
not let it. The message is already a ``multipart/alternative`` with the
headers we intend; handing SES the rendered bytes means the same
``EmailMessage`` goes out byte-identically whichever transport carries
it, so a customer switching from SMTP to the API does not get subtly
different mail.
"""

from __future__ import annotations

import asyncio
from email.message import EmailMessage
import logging

import boto3

from app.config import settings
from app.services.email.transports.base import EmailTransport

logger = logging.getLogger(__name__)


class SesTransport(EmailTransport):
    """Sends through the SES API using the ambient AWS credentials."""

    def __init__(
        self,
        *,
        region: str | None = None,
        configuration_set: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ) -> None:
        self._region = region if region is not None else settings.ses_region
        self._configuration_set = configuration_set if configuration_set is not None else settings.ses_configuration_set
        self._access_key_id = access_key_id if access_key_id is not None else settings.aws_access_key_id
        self._secret_access_key = secret_access_key if secret_access_key is not None else settings.aws_secret_access_key

    @property
    def description(self) -> str:
        how = 'static credentials' if self._access_key_id else 'the ambient AWS credential chain'
        return f'SES API in {self._region or "the default region"} via {how}'

    async def send(self, message: EmailMessage) -> bool:
        # boto3 is synchronous. Rather than take on aioboto3 for one
        # call, it goes through ``asyncio.to_thread`` — the same boundary
        # the S3 changeset backend uses, so both read alike.
        try:
            message_id = await asyncio.to_thread(self._send_sync, message)
        except Exception as exc:  # noqa: BLE001 — a send must not escape into a background task
            logger.error('SES rejected the message: %s', _explain(exc))
            return False

        logger.info('Email sent via SES to %s (message id %s)', message['To'], message_id)
        return True

    # ── Sync body, run in a worker thread ────────────────────────────

    def _client(self):
        return boto3.client(
            'sesv2',
            region_name=self._region or None,
            # Omitted entirely when unset, so boto3 falls through to the
            # credential chain. Passing None explicitly would do the same,
            # but being explicit here is what makes the IAM-role path
            # readable rather than accidental.
            **(
                {
                    'aws_access_key_id': self._access_key_id,
                    'aws_secret_access_key': self._secret_access_key,
                }
                if self._access_key_id
                else {}
            ),
        )

    def _send_sync(self, message: EmailMessage) -> str:
        request: dict = {
            'Content': {'Raw': {'Data': message.as_bytes()}},
        }
        # SES reads the envelope from the raw message, so From and To are
        # not repeated here. A configuration set is, when the customer
        # uses one for bounce and complaint handling.
        if self._configuration_set:
            request['ConfigurationSetName'] = self._configuration_set

        response = self._client().send_email(**request)
        return str(response.get('MessageId', 'unknown'))


def _explain(exc: Exception) -> str:
    """Turn a botocore error into something an operator can act on.

    The AWS error code is the useful part — ``MessageRejected`` for an
    unverified sender and ``AccountSendingPaused`` for a suspended
    account point at completely different fixes, and the default
    ``str(exc)`` buries both in a paragraph.
    """
    response = getattr(exc, 'response', None)
    if isinstance(response, dict):
        error = response.get('Error', {})
        code = error.get('Code')
        if code:
            return f'{code}: {error.get("Message", "")}'.strip()
    return f'{type(exc).__name__}: {exc}'
