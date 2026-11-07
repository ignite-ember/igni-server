"""The Article 15 context, built from what this deployment is doing."""

from __future__ import annotations

from app.config import Settings, settings as app_settings
from app.services.email.delivery import EmailDelivery
from app.services.subject_access.schemas import ProcessingContext, Retention


class ProcessingDescription:
    """Purposes, categories, recipients, retention and source for one deployment.

    Generated rather than boilerplate: retention comes from the settings
    actually in force, and the recipients reflect the providers that are
    actually configured. A static paragraph would go stale and would be
    the same paragraph for a deployment with no email configured as for
    one relaying through SES.

    `settings` defaults to the process-wide object, so a test that
    monkeypatches it is seen here. `EmailDelivery.enabled` always reads
    the process-wide settings, so injecting a fresh `Settings` without a
    matching `email` changes everything but the "is email on" decision.
    """

    def __init__(self, *, settings: Settings = app_settings, email: EmailDelivery | None = None) -> None:
        self._settings = settings
        self._email = email or EmailDelivery()

    def describe(self) -> ProcessingContext:
        return ProcessingContext(
            controller=(
                'The organisation operating this igni deployment. igni is self-hosted: '
                'the software vendor holds none of this data and has no access to it.'
            ),
            purposes=[
                'authenticating the person and deciding what they may access',
                'recording who performed privileged actions, for security review',
                'attributing group configuration to whoever made it',
                'accounting for model usage',
            ],
            categories=[
                'identity and contact data (name, email address)',
                'authentication metadata (method, last sign-in)',
                'authorisation data (organisation membership, role, status)',
                'activity records (audit events, model usage)',
            ],
            recipients=self._recipients()
            or [
                'none. No email relay and no object store are configured, so nothing '
                'about this person is sent anywhere outside this deployment.'
            ],
            retention=Retention(
                audit_events=(
                    f'{self._settings.audit_retention_days} days'
                    if self._settings.audit_retention_days > 0
                    else 'kept indefinitely — no retention window is configured'
                ),
                everything_else=(
                    'kept until the account or the organisation is deleted, or the person exercises erasure'
                ),
            ),
            source=(
                'Provided by the person when they signed up or accepted an invitation, '
                'or by an administrator who invited them.'
            ),
            rights='Rectification and erasure are available; erasure via POST /v1/portal/erasure (GDPR Art. 17).',
        )

    def _recipients(self) -> list[str]:
        """Who personal data is sent to.

        The same test the sending path uses, so the report cannot
        disagree with what is sent.
        """
        if not self._email.enabled:
            return []
        if self._settings.email_backend == 'ses':
            region = self._settings.ses_region or "the AWS SDK's default region"
            return [f'Amazon SES in {region}, for sign-in codes and invitations']
        return [f'the SMTP relay at {self._settings.smtp_host}, for sign-in codes and invitations']
