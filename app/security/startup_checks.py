"""Refuse to serve a deployment that cannot authenticate anybody.

Every setting here has a safe-looking default that is not safe, which is
the whole reason this file exists. A missing secret is not a
misconfiguration the server can carry on through — it is an open door,
and the failure mode is silent: tokens still verify, requests still
succeed, and nothing in a log says why.

The check runs at **startup**, not at import. ``app/config.py``
constructs ``settings`` at module scope, so raising there would make
importing configuration depend on deployment posture and take every test
with it. "The server must not start" is the actual requirement, and
``lifespan`` is where that belongs.

Rules are graded by ``app_env`` — the same signal ``main.py`` uses to
decide whether to expose ``/docs``. Anything that is not ``dev`` is
treated as serving real users.

Only settings are read here. The checks on secrets and keys are in
``app/security/secret_checks.py``; the ones that need the database are
in ``app/security/database_checks.py``.
"""

from __future__ import annotations

import logging

from app.config import Settings, settings as app_settings
from app.security.deployment_findings import DeploymentFindings, UnsafeDeployment
from app.security.secret_checks import SecretChecks
from app.services.email.delivery import EmailDelivery

logger = logging.getLogger(__name__)


class SettingsChecks:
    """Every check on this deployment's settings, as one pass.

    `settings` and `email` default to the process-wide objects; a test
    may pass its own rather than patching a module global.
    """

    def __init__(
        self,
        *,
        settings: Settings = app_settings,
        email: EmailDelivery | None = None,
        secrets: SecretChecks | None = None,
    ) -> None:
        self._settings = settings
        self._email = email or EmailDelivery(settings=settings)
        self._secrets = secrets or SecretChecks(settings=settings)

    def check(self, *, raise_on_unsafe: bool = True) -> list[str]:
        """Return the problems found; raise on the fatal ones outside dev.

        Logs the warnings. Fatal problems raise :class:`UnsafeDeployment`
        unless *raise_on_unsafe* is False, which exists so a test can
        assert on the message rather than on an exception type alone.
        """
        findings = self.assess()

        for warning in findings.warnings:
            logger.warning('Deployment check: %s', warning)

        if findings.fatal and raise_on_unsafe:
            raise UnsafeDeployment(
                'Refusing to start — this deployment cannot authenticate anybody:\n\n'
                + '\n\n'.join(findings.fatal)
                + '\n\nAPP_ENV=dev downgrades the others to warnings for local work; '
                'SECRET_ENCRYPTION_KEY is required everywhere.'
            )

        return findings.all()

    def notices(self) -> list[str]:
        """The startup warnings, for the portal's super admins (`GET /portal/deployment/notices`).

        A warning in the log reaches whoever reads the log, which on a real
        deployment is often not the person who can act on it — and for
        ``LOG_SIGN_IN_CODES`` (F35) the log is the exposure itself. The same
        checks, worded the same way, are shown to the people who run the
        deployment. Settings are read at startup, so the answer does not
        change while the process runs; it is recomputed rather than cached so
        there is no second copy to fall out of step. Not logged again here.
        """
        return self.assess().warnings

    def assess(self) -> DeploymentFindings:
        """Every check, without logging or raising."""
        findings = DeploymentFindings()
        self._secrets.encryption_key(findings)
        self._issuer(findings)
        self._portal_auth_bypass(findings)
        self._undeliverable_codes(findings)
        self._secrets.shared_secret_lengths(findings)
        self._secrets.break_glass_key(findings)
        self._localhost_urls(findings)
        self._secrets.unfinished_rotation(findings)
        self._partial_oidc(findings)
        self._insecure_oidc_issuer(findings)
        return findings

    def _is_development(self) -> bool:
        return self._settings.app_env.lower() == 'dev'

    def _issuer(self, findings: DeploymentFindings) -> None:
        """``PUBLIC_BASE_URL`` must be HTTPS outside dev.

        It is where CodeIndex, Artifacts and every other registered client
        fetch this server's public keys. Over plain HTTP, anybody on the
        path can substitute their own keys and mint a token for any person
        in any organisation — the same reason an ``http://`` OIDC issuer is
        refused.
        """
        public_base_url = self._settings.public_base_url
        if not self._is_development() and not public_base_url.startswith('https://'):
            findings.refuse(
                f'PUBLIC_BASE_URL is {public_base_url!r}. Services fetch this '
                f"server's signing keys from it, and over plain HTTP those keys can be replaced in transit.",
                "set PUBLIC_BASE_URL to the server's https:// address.",
            )

    def _portal_auth_bypass(self, findings: DeploymentFindings) -> None:
        """The portal auth bypass is refused outside dev.

        It makes every portal request a hardcoded admin in a demo org. It
        defaults to False, but it is set in the local compose file and in
        .env, so it travels easily to somewhere it must not be.
        """
        if self._settings.portal_auth_bypass and not self._is_development():
            findings.refuse(
                f'PORTAL_AUTH_BYPASS is enabled with APP_ENV={self._settings.app_env!r}. Every '
                f'portal request would be treated as an administrator with no credentials.',
                'unset PORTAL_AUTH_BYPASS, or set APP_ENV=dev if this really is a development deployment.',
            )

    def _undeliverable_codes(self, findings: DeploymentFindings) -> None:
        """Where one-time codes go when there is no relay.

        With no relay, ``record_undeliverable_code`` can write sign-in and
        password-reset codes into the application log so an operator can
        pass them on — never back to the caller, who could otherwise
        request one for any address and read it straight back.

        F35: that used to be the *default*. ``email_enabled`` is False and
        ``smtp_host`` empty out of the box, so a deployment that had not got
        to email yet was writing working credentials into logs that are
        shipped to an aggregator, kept for months and readable by more
        people than should be able to sign in as anybody. It is now an
        explicit ``LOG_SIGN_IN_CODES=true``, and while it is on this says so
        on every boot, in every environment — it is a deliberate exposure,
        and the kind that outlives the first setup it was turned on for.

        No relay with the flag off is not a refusal: a deployment that
        signs everybody in through SSO needs none. But a code requested
        there reaches nobody, and saying so at boot beats a user waiting
        for an email that is never sent.
        """
        relay = self._email.enabled
        log_codes = self._settings.log_sign_in_codes
        if log_codes and not relay:
            findings.warn(
                'LOG_SIGN_IN_CODES is on and no mail relay is configured, so every sign-in, sign-up '
                'and password-reset code is written to the application log. Anyone who can read '
                'these logs can sign in as any user and reset any password — treat them as '
                'credential material, and turn LOG_SIGN_IN_CODES off once a relay is configured.'
            )
        elif log_codes:
            findings.warn(
                'LOG_SIGN_IN_CODES is on. A relay is configured, so codes are emailed and it has '
                'no effect today — but if the relay is ever removed, codes start going to the '
                'log without anybody choosing it again. Unset it.'
            )
        elif not relay and not self._is_development():
            findings.warn(
                'No mail relay is configured (EMAIL_ENABLED / SMTP_HOST, or EMAIL_BACKEND=ses), and '
                'LOG_SIGN_IN_CODES is off, so sign-in and password-reset codes reach nobody. '
                'Configure a relay; or, for first setup only, set LOG_SIGN_IN_CODES=true to have '
                'codes written to the server log — where anyone who can read it can use them.'
            )

    def _localhost_urls(self, findings: DeploymentFindings) -> None:
        """Public URLs still pointing at a developer's laptop.

        These default to localhost and are absent from ``dist.env``, so a
        deployment that follows the template inherits them. They are not a
        security hole — they break in the open — but they break in places
        nobody is watching: an invite email carrying a link to
        ``localhost:3000`` sends fine and is useless to whoever receives
        it, and an OAuth callback pointing at a laptop is rejected by the
        provider rather than by us.
        """
        if self._is_development():
            return
        for name, value, breaks in (
            ('PORTAL_BASE_URL', self._settings.portal_base_url, 'invite links and portal links in error messages'),
            ('OAUTH_REDIRECT_BASE_URL', self._settings.oauth_redirect_base_url, 'OAuth provider callbacks'),
            ('OAUTH_FRONTEND_BASE_URL', self._settings.oauth_frontend_base_url, 'post-login redirects'),
        ):
            if 'localhost' in value or '127.0.0.1' in value:
                findings.warn(
                    f'{name} is {value!r} with APP_ENV={self._settings.app_env!r}, so {breaks} '
                    f"point at this machine. Set it to the deployment's public URL."
                )

    def _partial_oidc(self, findings: DeploymentFindings) -> None:
        """OIDC, half-configured.

        `/v1/auth/oidc` answers 404 unless all three of issuer, client id
        and client secret are set — the same fail-closed shape as node
        registration. Which is right, and silent: an operator who set two
        of the three sees an endpoint that does not exist and concludes the
        feature is missing rather than that their configuration is
        incomplete. Saying so at startup costs nothing.
        """
        parts = (
            ('OIDC_ISSUER', self._settings.oidc_issuer),
            ('OIDC_CLIENT_ID', self._settings.oidc_client_id),
            ('OIDC_CLIENT_SECRET', self._settings.oidc_client_secret),
        )
        missing = sorted(name for name, value in parts if not value)
        if missing and len(missing) < len(parts):
            findings.warn(
                f'OIDC is partly configured: {missing} not set, so SSO is disabled and '
                f'/v1/auth/oidc answers 404. Set all three, or none.'
            )

    def _insecure_oidc_issuer(self, findings: DeploymentFindings) -> None:
        """An `http://` issuer is fatal outside dev.

        Not a preference. The discovery document and the signing keys
        arrive over that connection, so anybody who can see the traffic can
        substitute their own keys and mint any user. Fatal rather than a
        warning: this is the trust anchor.
        """
        issuer = self._settings.oidc_issuer
        if not issuer or issuer.startswith('https://'):
            return
        host = issuer.removeprefix('http://').split('/')[0].split(':')[0]
        if host in ('localhost', '127.0.0.1', '::1'):
            return
        problem = (
            f'OIDC_ISSUER is {issuer!r}, which is not HTTPS. The signing keys '
            'for every SSO login arrive over that connection, so anybody who can see the '
            'traffic can substitute their own and sign in as any user.'
        )
        fix = 'use an https issuer URL.'
        if self._is_development():
            findings.warn(f'INSECURE (allowed because APP_ENV=dev): {DeploymentFindings.explain(problem, fix)}')
        else:
            findings.refuse(problem, fix)
