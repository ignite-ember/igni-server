"""A deployment that cannot authenticate anybody must not start.

The bug these were first written for was live: an HMAC signing secret
that defaulted to ``''``, so the server issued tokens *and accepted
tokens anyone else signed the same way*. Tokens are now RS256 under a
key the server generates and keeps encrypted, so there is no signing
secret to leave empty — but the key that encrypts it, and the URL its
public half is fetched from, are the same kind of setting: harmless-
looking defaults that are not safe.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import logging

from cryptography.fernet import Fernet
import pytest

from app.config import settings
from app.security.deployment_findings import UnsafeDeployment
from app.security.startup_checks import SettingsChecks

#: A real Fernet key: the check parses it.
GOOD_KEY = Fernet.generate_key().decode()

#: A prod deployment with *nothing* wrong, so a test asserting on one
#: warning is not accidentally asserting on the others.
#:
#: Complete on purpose — ``app_env`` and both keys included, not only the
#: fields that later checks added. It began as a subset, with each test
#: also passing its own key, and that shape broke
#: repeatedly as checks accumulated: a test written with ``_clean`` alone
#: silently lacked the keys, while adding the keys here collided with the
#: tests that passed them as well. One complete dict plus ``_clean`` for
#: the overrides is the version that stops needing maintenance.
#:
#: Every new check that fires on a default belongs here.
CLEAN_PROD = {
    'app_env': 'prod',
    'secret_encryption_key': GOOD_KEY,
    'public_base_url': 'https://api.igni.example.com',
    'portal_auth_bypass': False,
    'portal_base_url': 'https://igni.example.com',
    'oauth_redirect_base_url': 'https://api.igni.example.com',
    'oauth_frontend_base_url': 'https://igni.example.com',
    'email_enabled': True,
    'email_backend': 'smtp',
    'smtp_host': 'smtp.example.com',
    'log_sign_in_codes': False,
    'node_registration_token': 'n' * 48,
}

#: The URL subset, kept for the tests that are specifically about URLs.
REAL_URLS = {
    'portal_base_url': CLEAN_PROD['portal_base_url'],
    'oauth_redirect_base_url': CLEAN_PROD['oauth_redirect_base_url'],
    'oauth_frontend_base_url': CLEAN_PROD['oauth_frontend_base_url'],
}


def _clean(**overrides: object) -> dict:
    """``CLEAN_PROD`` with specific keys replaced.

    Needed because a test that wants one field wrong has to override it
    *within* the clean set — passing it alongside ``**CLEAN_PROD`` is a
    duplicate keyword argument, which is a TypeError rather than a
    failed assertion and reads like an unrelated break.
    """
    return {**CLEAN_PROD, **overrides}


@contextmanager
def _settings(**overrides: object) -> Iterator[None]:
    """Temporarily override settings fields.

    ``object.__setattr__`` because pydantic-settings guards assignment;
    the module-level ``settings`` singleton is what the check reads, so
    it has to be the thing that changes.
    """
    saved = {key: getattr(settings, key) for key in overrides}
    for key, value in overrides.items():
        object.__setattr__(settings, key, value)
    try:
        yield
    finally:
        for key, value in saved.items():
            object.__setattr__(settings, key, value)


class TestTheEncryptionKey:
    """It encrypts the server's own token-signing key, so without it the
    server cannot sign anybody in — in dev as much as anywhere."""

    @pytest.mark.parametrize('env', ['prod', 'stage', 'dev'])
    def test_an_empty_key_refuses_to_start_everywhere(self, env: str):
        with _settings(**_clean(app_env=env, secret_encryption_key='')):
            with pytest.raises(UnsafeDeployment) as caught:
                SettingsChecks().check()

        message = str(caught.value)
        assert 'SECRET_ENCRYPTION_KEY' in message
        assert 'signing key' in message
        # And how to fix it, because a refusal with no remedy just gets
        # worked around.
        assert 'Fernet.generate_key' in message

    def test_a_key_that_is_not_a_fernet_key_refuses_too(self):
        """``changeme`` passes a non-empty check and decrypts nothing."""
        with _settings(**_clean(secret_encryption_key='changeme')):
            with pytest.raises(UnsafeDeployment, match='not a valid Fernet key'):
                SettingsChecks().check()

    def test_a_real_key_starts_clean(self):
        with _settings(**_clean()):
            assert SettingsChecks().check() == []


class TestTheIssuer:
    """Services fetch the server's public keys from ``PUBLIC_BASE_URL``."""

    def test_plain_http_refuses_outside_dev(self):
        with _settings(**_clean(public_base_url='http://api.igni.example.com')):
            with pytest.raises(UnsafeDeployment, match='PUBLIC_BASE_URL'):
                SettingsChecks().check()

    def test_dev_may_use_plain_http(self):
        with _settings(**_clean(app_env='dev', public_base_url='http://localhost:8000')):
            assert not any('PUBLIC_BASE_URL' in problem for problem in SettingsChecks().check())


class TestThePortalBypass:
    def test_it_refuses_outside_dev(self):
        """``portal_auth_bypass`` makes every portal request a hardcoded
        admin. It defaults to False, but it is set in the local compose
        file and in .env, so it travels."""
        with _settings(
            **_clean(
                portal_auth_bypass=True,
            )
        ):
            with pytest.raises(UnsafeDeployment) as caught:
                SettingsChecks().check()

        message = str(caught.value)
        assert 'PORTAL_AUTH_BYPASS' in message
        assert 'administrator' in message

    def test_it_is_allowed_in_dev(self):
        with _settings(
            **_clean(
                **_clean(
                    app_env='dev',
                    portal_auth_bypass=True,
                )
            )
        ):
            assert SettingsChecks().check() == []


class TestTheDefaultsThemselves:
    """The defaults are what a deployment gets when nobody says
    otherwise, so they are worth asserting directly."""

    def test_app_env_defaults_to_prod(self):
        """So an unset environment is treated as serving real users. A
        default of 'dev' would make every check above opt-in."""
        from app.config import Settings

        assert Settings.model_fields['app_env'].default == 'prod'

    def test_the_bypass_defaults_to_off(self):
        from app.config import Settings

        assert Settings.model_fields['portal_auth_bypass'].default is False

    def test_more_than_one_problem_is_reported_at_once(self):
        """A deploy loop that fixes one thing per restart is a bad way to
        spend an afternoon."""
        with _settings(**_clean(secret_encryption_key='', public_base_url='http://x', portal_auth_bypass=True)):
            with pytest.raises(UnsafeDeployment) as caught:
                SettingsChecks().check()

        message = str(caught.value)
        assert 'SECRET_ENCRYPTION_KEY' in message
        assert 'PUBLIC_BASE_URL' in message
        assert 'PORTAL_AUTH_BYPASS' in message


class TestPublicUrls:
    """Defaults pointing at a laptop, in a deployment that is not one.

    Warnings rather than refusals: nothing here is a security hole. They
    earn a check because of *where* they fail — an invite email with a
    ``localhost:3000`` link sends successfully and is useless on arrival,
    which is the kind of break nobody is watching for.
    """

    def test_localhost_urls_warn_outside_dev(self):
        with _settings(
            **_clean(
                **_clean(
                    portal_base_url='http://localhost:3000',
                    oauth_redirect_base_url='http://localhost:5173',
                    oauth_frontend_base_url='http://localhost:5173',
                ),
            )
        ):
            problems = SettingsChecks().check()

        assert len(problems) == 3
        joined = ' '.join(problems)
        for name in ('PORTAL_BASE_URL', 'OAUTH_REDIRECT_BASE_URL', 'OAUTH_FRONTEND_BASE_URL'):
            assert name in joined
        # Each says what breaks, so the reader can tell which of the
        # three they actually need.
        assert 'invite links' in joined
        assert 'OAuth provider callbacks' in joined

    def test_real_urls_are_quiet(self):
        with _settings(**_clean()):
            assert SettingsChecks().check() == []

    def test_dev_is_not_nagged(self):
        with _settings(**_clean(app_env='dev')):
            assert SettingsChecks().check() == []


class TestSharedSecretsThatAreTooShort:
    """Length is all these have.

    Both are compared with ``compare_digest`` and both answer 401 on a
    mismatch — correct — but neither has an attempt limit, because neither
    is a user-facing login. Nothing checked their length, and
    ``NODE_REGISTRATION_TOKEN=test`` authenticated a GPU node
    registration when it was tried.
    """

    def test_a_short_node_token_warns(self):
        with _settings(
            **_clean(
                **_clean(node_registration_token='test'),
            )
        ):
            problems = SettingsChecks().check()

        assert len(problems) == 1
        assert 'NODE_REGISTRATION_TOKEN' in problems[0]
        # Says what it guards and why length is the whole story, so the
        # reader can judge it rather than just obey.
        assert 'GPU node registration' in problems[0]
        assert 'attempt limit' in problems[0]

    def test_unset_is_not_a_problem(self):
        """Leaving it unset is supported and disables the surface it
        guards — the node API answers 404 rather than advertising a
        disabled endpoint. Warning about that would be noise."""
        with _settings(
            **_clean(
                **_clean(node_registration_token=''),
            )
        ):
            assert SettingsChecks().check() == []

    def test_a_long_token_is_quiet(self):
        with _settings(
            **_clean(
                **_clean(node_registration_token='n' * 48),
            )
        ):
            assert SettingsChecks().check() == []


class TestWhereOneTimeCodesGoWithNoRelay:
    """F35. Codes in the log used to be the default.

    With no SMTP relay, ``record_undeliverable_code`` logged sign-in and
    password-reset codes so an operator could pass them on, and
    ``email_enabled`` is False out of the box — so a deployment that had
    not got to email yet was writing credentials into logs that are
    shipped to an aggregator, retained for months and readable by a wider
    group than the people who should be able to sign in as anybody.

    Now it takes ``LOG_SIGN_IN_CODES=true``. While that is on it is said on
    every boot, in every environment; with it off and no relay, the boot
    says codes reach nobody and how to get in.
    """

    def test_off_with_no_relay_says_codes_reach_nobody_and_how_to_get_in(self):
        with _settings(**_clean(email_enabled=False, smtp_host='')):
            problems = SettingsChecks().check()

        assert len(problems) == 1
        assert 'reach nobody' in problems[0]
        # The documented way in for a deployment with no email.
        assert 'LOG_SIGN_IN_CODES=true' in problems[0]

    def test_a_configured_relay_is_quiet(self):
        with _settings(**_clean()):
            assert SettingsChecks().check() == []

    @pytest.mark.parametrize('env', ['prod', 'stage', 'dev'])
    def test_opting_in_is_warned_on_every_boot_everywhere(self, env: str):
        """Dev included: it is a deliberate exposure, and the kind that
        outlives the first setup it was turned on for."""
        with _settings(**_clean(app_env=env, email_enabled=False, smtp_host='', log_sign_in_codes=True)):
            problems = SettingsChecks().check()

        (warning,) = [p for p in problems if 'LOG_SIGN_IN_CODES' in p]
        # Names the consequence, not just the setting: an operator has to
        # be able to judge how their own log handling stacks up.
        assert 'sign in as any user' in warning
        assert 'credential material' in warning

    def test_the_warning_reaches_the_log(self, caplog):
        with _settings(**_clean(email_enabled=False, smtp_host='', log_sign_in_codes=True)):
            with caplog.at_level(logging.WARNING):
                SettingsChecks().check()

        assert any(r.levelno == logging.WARNING and 'LOG_SIGN_IN_CODES is on' in r.getMessage() for r in caplog.records)

    def test_left_on_beside_a_relay_is_flagged(self):
        """Inert today; but remove the relay and codes go back to the log
        without anybody choosing it again."""
        with _settings(**_clean(log_sign_in_codes=True)):
            problems = SettingsChecks().check()

        assert len(problems) == 1
        assert 'Unset it' in problems[0]

    def test_the_flag_alone_is_not_enough(self):
        """``email_enabled`` on with no host is a misconfiguration that
        otherwise fails once per message in a background task, where
        nobody is watching — ``delivery.py`` says so, and this check has
        to agree with it."""
        with _settings(**_clean(email_enabled=True, smtp_host='')):
            problems = SettingsChecks().check()

        assert len(problems) == 1
        assert 'reach nobody' in problems[0]

    def test_ses_is_a_relay_without_an_smtp_host(self):
        with _settings(**_clean(email_enabled=True, email_backend='ses', smtp_host='')):
            assert SettingsChecks().check() == []

    def test_dev_with_it_off_is_not_nagged(self):
        """The local compose file turns it on; a dev run without it is a
        developer's choice, not a deployment's surprise."""
        with _settings(**_clean(app_env='dev', email_enabled=False, smtp_host='')):
            assert SettingsChecks().check() == []

    def test_the_code_is_never_returned_to_the_caller(self):
        """The part that was already right, pinned so it stays right.

        ``record_undeliverable_code`` returns None and logs. If it grew a
        return value, the caller — an anonymous request carrying only an
        email address — could read back a code for anybody.
        """
        import inspect

        from app.services.email.delivery import EmailDelivery

        signature = inspect.signature(EmailDelivery.record_undeliverable_code)
        assert signature.return_annotation in (None, 'None')
        assert 'return code' not in inspect.getsource(EmailDelivery.record_undeliverable_code)
