"""A deployment with no Google Cloud starts, and the shipped template boots.

F97, and §41's first marketplace blocker. The distribution target is
self-hosted through the AWS and GCP marketplaces, which makes "starts on
AWS" a product requirement rather than a nicety.

Two bugs of the same shape motivated this, and neither was a bug in any
code path — both were in configuration that outlived what it configured:

* `changeset_gcs_bucket` was `Field(..., min_length=1)`: the only field
  in the whole configuration with no default. The changeset pipeline it
  belonged to had already moved to igni__code-index, so the setting
  configured nothing and refused to boot anywhere without a GCS bucket
  name to hand it;
* `dist.env` shipped `EMAIL_ENABLED=` — blank. Blank is a fine value for
  a string ("fill this in") and a validation error for a boolean, so a
  customer who copied the template verbatim could not start either.

The second is why this file tests the template rather than only the
model. A setting can be individually correct while the artefact a
customer is told to copy does not validate.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

from dotenv import dotenv_values
import pytest

from app.config import Settings

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_TEMPLATE = _ROOT / 'dist.env'


class TestNothingGoogleSpecificIsRequired:
    def test_no_setting_is_required(self):
        """A required setting is a boot failure waiting for the first
        deployment that does not happen to have it.

        There is a place for refusing to start on a missing value — see
        `app/security/startup_checks.py`, which refuses without
        `SECRET_ENCRYPTION_KEY`. That refusal explains itself and names
        the command that fixes it. A bare `Field(...)` raises `ValidationError`
        at import, before any check runs and before any log line, which
        is the worst version of the same idea.
        """
        required = sorted(name for name, f in Settings.model_fields.items() if f.is_required())

        assert not required, (
            f'these settings have no default and will raise at import: {required}. '
            'Give them a default and enforce them in startup_checks, where a '
            'deployment gets a sentence rather than a pydantic traceback.'
        )

    def test_the_configuration_constructs_with_an_aws_shaped_environment(self):
        """Postgres and Redis and nothing else. No bucket, no project id,
        no service account."""
        settings = Settings(
            _env_file=None,
            postgres_host='db.internal',
            postgres_password='x',
            redis_host='redis.internal',
        )

        assert settings.postgres_host == 'db.internal'

    def test_the_app_imports_in_an_environment_stripped_of_everything(self, tmp_path):
        """A stripped environment, in a subprocess, because the test
        process inherits a working one and would prove nothing.

        This is the assertion that actually failed before F97: importing
        `app.main` with no `CHANGESET_GCS_BUCKET` anywhere raised
        `ValidationError` from `app.config` at import time.

        **It runs from `tmp_path`, and that is the point.** The first
        version ran with `cwd=_ROOT` and passed — because
        `model_config['env_file']` is the relative path `.env`, so
        pydantic-settings loaded the developer's own `.env` from the repo
        root, which has a bucket in it. The test would have passed
        against the unfixed code. Running from a directory with no `.env`
        is what makes it an assertion rather than a formality; the repo
        is reachable via `PYTHONPATH` instead.
        """
        assert not (tmp_path / '.env').exists()

        result = subprocess.run(
            [
                sys.executable,
                '-c',
                'import sys, app.main; print("imported", type(app.main.app).__name__); '
                'print("cloud sdks loaded:", sorted(m for m in ("google.auth", "azure.identity") if m in sys.modules))',
            ],
            cwd=tmp_path,
            env={
                'PATH': '/usr/bin:/bin',
                'PYTHONPATH': str(_ROOT),
                'POSTGRES_HOST': 'db.internal',
                'POSTGRES_PASSWORD': 'x',
                'REDIS_HOST': 'redis.internal',
            },
            capture_output=True,
            text=True,
            timeout=180,
        )

        assert result.returncode == 0, (
            f'the app does not import in an environment with no Google Cloud variables:\n{result.stderr[-2000:]}'
        )
        assert 'imported FastAPI' in result.stdout, result.stdout
        # The cloud credential libraries load only when a backend of that
        # cloud is configured (app/services/ai/providers): booting loads none.
        assert 'cloud sdks loaded: []' in result.stdout, result.stdout

    def test_there_is_no_google_cloud_sdk_in_the_dependency_set(self):
        """The air-gap claim (DP-5) and this one are the same claim seen
        from two sides. A cloud SDK in the lockfile is the thing that
        makes a required cloud setting look reasonable."""
        lock = (_ROOT / 'uv.lock').read_text()

        assert 'name = "boto3"' in lock, (
            'boto3 is expected — SES is a supported email backend. If this fails '
            'the assertion below is no longer measuring what it thinks.'
        )

        # Allowed by name, each for its reason, so anything else beginning
        # `google` still fails here:
        #
        # - `googleapis-common-protos` is the protobuf definitions of
        #   `google.rpc.Status`, which the OTLP exporters decode a collector's
        #   error replies with. It names no Google service and reaches none.
        # - `google-auth` is credentials only — no storage, no service client.
        #   It is declared (pyproject) but imported only inside the Vertex token
        #   sources (app/services/ai/providers/google.py), so a deployment with
        #   no Vertex backend never loads it; the boot test above holds that.
        not_a_cloud_sdk = {'name = "googleapis-common-protos"', 'name = "google-auth"'}
        google = [
            line for line in lock.splitlines() if line.startswith('name = "google') and line not in not_a_cloud_sdk
        ]
        assert not google, (
            f'a Google Cloud SDK is back in the dependency set: {google}. Object '
            'storage belongs to igni__code-index; see docs/service-contract.md.'
        )


class TestTheShippedTemplateBoots:
    """`dist.env` is what a customer copies. If it does not validate, the
    quality of the settings model is irrelevant."""

    def test_the_template_parses(self):
        values = dotenv_values(_TEMPLATE)

        assert len(values) > 20, f'only {len(values)} keys parsed out of dist.env — the parse is wrong'

    def test_the_template_validates_as_written(self, monkeypatch):
        """Verbatim, with every placeholder left blank. This is the state
        a deployment is in five minutes after `cp dist.env .env`, and it
        must fail on *missing operator input* — via startup checks, with
        an explanation — rather than on a type error.

        **`monkeypatch.delenv` is what makes this test about the file.**
        Pydantic gives the process environment priority over `_env_file`,
        and CI runs the suite with `APP_ENV=dev` — so this read `dev` off
        the environment, compared it to `prod`, and failed. It had failed
        since it was written, and nobody saw it because the last nine
        days of work have not been pushed.

        The failure was real and the assertion was right; the mechanism
        was measuring the runner instead of the template. Clearing the
        variable is the difference between "what does this deployment
        have configured" and "what does the file we ship say" — and only
        the second one is this test's business.
        """
        monkeypatch.delenv('APP_ENV', raising=False)

        settings = Settings(_env_file=_TEMPLATE)

        assert settings.app_env == 'prod', 'the template should ship the production default'

    def test_the_environment_cannot_disguise_the_template(self, monkeypatch):
        """The guard on the guard above.

        If `delenv` stopped working — or somebody replaced it with a
        fixture that sets `APP_ENV=prod` — the assertion would pass
        against the environment rather than the file, which is the bug
        it was just fixed for. Setting the variable to something the
        template does not contain proves the file is what is read.
        """
        monkeypatch.setenv('APP_ENV', 'dev')

        leaked = Settings(_env_file=_TEMPLATE)
        assert leaked.app_env == 'dev', (
            'the process environment no longer overrides `_env_file`. If pydantic '
            'changed that precedence this is good news, and the `delenv` above is '
            'now belt-and-braces rather than load-bearing.'
        )

        monkeypatch.delenv('APP_ENV')
        assert Settings(_env_file=_TEMPLATE).app_env == 'prod'

    def test_every_key_in_the_template_configures_something(self):
        """Four keys named a GCP project and three Secret Manager secrets
        that stopped being settings when the AI key pool moved out.
        `extra: 'ignore'` swallowed them silently, so the template was
        advertising a Google Cloud dependency this repo does not have —
        to exactly the reader most likely to believe it.
        """
        fields = set(Settings.model_fields)
        unknown = sorted(k for k in dotenv_values(_TEMPLATE) if k.lower().split('__')[0] not in fields)

        assert not unknown, (
            f'dist.env sets variables that configure nothing: {unknown}. '
            "`extra: 'ignore'` means a deployment gets no warning either."
        )

    @pytest.mark.parametrize(
        'name',
        [name for name, f in Settings.model_fields.items() if f.annotation is bool],
        ids=lambda n: n,
    )
    def test_no_boolean_is_left_blank(self, name):
        """The `EMAIL_ENABLED=` bug, as a rule. A blank string is a
        reasonable placeholder for every other type in this file and a
        hard failure for these."""
        value = dotenv_values(_TEMPLATE).get(name.upper())

        if value is None:
            pytest.skip(f'{name} is not in the template')
        assert value.strip(), (
            f'{name.upper()} is blank in dist.env. It is a boolean, so pydantic '
            'rejects the empty string and the template will not boot. Ship the '
            'real default instead.'
        )


def test_the_gcs_settings_are_gone_and_stay_gone():
    """Named explicitly, because the failure mode was a setting outliving
    its subsystem — which no general rule above would have caught while
    the value was merely *present* rather than required."""
    assert 'changeset_gcs_bucket' not in Settings.model_fields
    assert 'changeset_gcs_prefix' not in Settings.model_fields

    source = (_ROOT / 'app/config.py').read_text()
    assert 'igni__code-index' in source, (
        'the comment explaining where object storage went has been removed; '
        'without it somebody will helpfully add the setting back'
    )


class TestNoTestLeavesTheEnvironmentBehind:
    """The template test above depends on `APP_ENV` being unset.

    Importing `app.main` refuses without one, so five test files set it
    and none put it back — which makes this file's result depend on test
    order. Environment variables beat an env file in pydantic-settings,
    so a leaked `dev` makes a correct template look wrong.

    It had never fired, and only by luck: every leaker sorts after this
    file, and the suite runs in collection order. Reading the schema at
    *collection* time for a `parametrize` beat that, because collection
    precedes every test — and a `-k` subset or a shard would too. The
    guard below does not depend on noticing that again.

    `tests/conftest.py`'s `the_app()` sets it and restores it. This is
    the rule that keeps the next caller from doing it by hand.
    """

    def test_nothing_sets_app_env_without_restoring_it(self):
        import pathlib
        import re

        tests = pathlib.Path(__file__).resolve().parent
        offenders = []
        for path in sorted(tests.glob('test_*.py')):
            text = path.read_text()
            for match in re.finditer(r"os\.environ(?:\.setdefault\(|\[)['\"]APP_ENV", text):
                # The helper in conftest is the one place allowed to,
                # because it is the one place that restores it.
                offenders.append(f'{path.name}:{text[: match.start()].count(chr(10)) + 1}')

        assert not offenders, (
            f'{offenders} set APP_ENV directly. Use `the_app()` from conftest, which restores '
            f'it — otherwise this file passes or fails depending on test order.'
        )

    def test_the_helper_really_restores_it(self):
        """A guard on the guard: if `the_app()` stopped restoring, every
        caller would be back to leaking and this file would be back to
        depending on order."""
        import os

        from tests.conftest import the_app

        before = os.environ.get('APP_ENV')
        assert the_app() is not None
        assert os.environ.get('APP_ENV') == before, (
            f'the_app() left APP_ENV as {os.environ.get("APP_ENV")!r}, was {before!r}'
        )
