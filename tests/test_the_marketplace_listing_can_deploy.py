"""The GCP listing can produce a deployment that starts. F109.

A Google Cloud Marketplace Kubernetes listing is three artefacts:
`marketplace/schema.yaml` describing the form a customer fills in, an
Application CRD so the deployment appears in the console as one thing,
and a deployer image that renders the chart with those answers.

The check worth having is the join between two of them. `charts/igni`
refuses to render without eight values — deliberately, because a
plausible wrong default for an image repository is how
`docker-compose.prod.yml` once pointed at a Docker Hub namespace that
was not ours. But **a refusal a customer cannot answer is a listing
whose deploy fails**, and nothing about either file makes that visible:
the schema is valid YAML, the chart is a valid chart, and the listing is
broken.

So the chart's refusals are extracted from its own template source and
matched against what the schema can supply. Adding a `fail` to the chart
without a corresponding parameter fails here.

**What this cannot check:** whether Google accepts the schema. That needs
`mpdev`, a marketplace project and a published deployer image. The
structure is a first draft; the parameter set is verified.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SCHEMA = _ROOT / 'marketplace/schema.yaml'
_CHART = _ROOT / 'charts/igni'
_HELM = shutil.which('helm')


@pytest.fixture(scope='module')
def schema() -> dict:
    return yaml.safe_load(_SCHEMA.read_text())


def _chart_refusals() -> set[str]:
    """Values `_helpers.tpl` refuses to render without.

    Read out of the template rather than listed here, so this cannot
    drift from the chart. The pattern is the value name at the start of
    each `fail` message, which is the convention every one of them
    follows — and the test below asserts the extraction found them,
    because a regex that matches nothing would make the coverage check
    pass vacuously.
    """
    source = (_CHART / 'templates/_helpers.tpl').read_text()
    names = set()

    for message in re.findall(r'\{\{-\s*fail\s+"([^"]+)"', source):
        match = re.match(r'([a-z][A-Za-z0-9.]*) is required', message)
        if match:
            names.add(match.group(1))

    return names


def _schema_can_supply(schema: dict) -> set[str]:
    """Every chart value the form can set, including via image mapping."""
    available = set(schema.get('properties') or {})

    images = (schema.get('x-google-marketplace') or {}).get('images') or {}
    for image in images.values():
        available |= set((image.get('properties') or {}))

    return available


class TestTheSchemaIsLoadable:
    """Everything below reads this file."""

    def test_it_parses(self, schema):
        assert 'properties' in schema
        assert 'x-google-marketplace' in schema

    def test_it_declares_the_schema_version_the_rest_assumes(self, schema):
        assert schema['x-google-marketplace']['schemaVersion'] == 'v2'

    def test_the_refusal_extraction_finds_something(self):
        """The coverage test below compares two sets. If this one is
        empty it passes against a schema that supplies nothing."""
        refusals = _chart_refusals()

        assert len(refusals) >= 4, f'only found {refusals} — the pattern no longer matches the chart'
        assert 'postgres.host' in refusals


class TestEveryRefusalCanBeAnswered:
    """The join. A chart that refuses and a form that cannot supply is a
    listing whose deploy fails, and both files look fine alone."""

    def test_the_form_covers_what_the_chart_demands(self, schema):
        missing = sorted(_chart_refusals() - _schema_can_supply(schema))

        assert not missing, (
            f'the chart refuses to render without {missing}, and the marketplace form '
            'cannot supply them. A customer would get a failed deploy with a message '
            'about a Helm value they never saw.'
        )

    def test_the_images_are_mapped_rather_than_typed_by_a_customer(self, schema):
        """Marketplace rewrites these to its own registry paths, which is
        what makes the chart's no-default rule workable: the customer
        never types a registry and the chart never guesses one."""
        images = schema['x-google-marketplace']['images']

        assert '' in images, 'the primary image must be the empty-string key'
        assert 'portal' in images, 'the portal image is not declared'

        for name, spec in images.items():
            for value, mapping in spec['properties'].items():
                assert mapping['type'] in (
                    'REPO_WITH_REGISTRY',
                    'FULL',
                    'TAG',
                    'REPO_WITHOUT_REGISTRY',
                ), f'{name}: {value} has an unrecognised image mapping {mapping}'

    def test_every_published_image_is_declared(self, schema):
        """A listing that ships fewer images than CD builds produces a
        deployment missing part of the product; one that declares more
        produces a deployment waiting on an image nobody pushes.

        Derived from `release.yml` rather than listed here — this test was
        named `test_both_...` and pinned the set to two, so adding the
        embedder failed it twice: once for the count, and once for the
        assertion that the count was still two.
        """
        release = (_ROOT / '.github/workflows/release.yml').read_text()
        pushed = set(re.findall(r'IMAGE="[^"]*/([A-Za-z0-9._-]+)"', release))

        assert pushed, 'no image build found in release.yml; this test is stale'
        assert len(schema['x-google-marketplace']['images']) == len(pushed), (
            f'release.yml pushes {sorted(pushed)} but the schema declares '
            f'{len(schema["x-google-marketplace"]["images"])} images'
        )

    def test_no_required_parameter_is_one_the_customer_cannot_know(self, schema):
        """`required` is what the form will not submit without. Every
        entry has to be something a customer can actually answer at
        deploy time — not a key we expect them to mint, which is why the
        encryption key is generated instead."""
        assert set(schema['required']) == {'name', 'namespace', 'postgres.host', 'redis.host'}

    def test_the_encryption_key_is_generated_not_requested(self, schema):
        """The chart and the server both refuse without a Fernet key —
        the server keeps its own signing key encrypted under it — and
        asking a person to mint one and paste it into a browser form is
        the worse of the two ways to satisfy that.

        A Fernet key is 32 bytes in base64. Thirty-two generated ASCII
        characters, base64-encoded, are exactly that: 44 characters, the
        length the chart checks. Any other length fails the deploy it
        was meant to enable."""
        assert 'secrets.jwtSecretKey' not in schema['properties'], 'the chart has no signing key to supply'
        key = schema['properties']['secrets.secretEncryptionKey']['x-google-marketplace']

        assert key['type'] == 'GENERATED_PASSWORD'
        assert key['generatedPassword']['length'] == 32
        assert key['generatedPassword']['base64'] is True

    def test_every_secret_is_masked_or_generated(self, schema):
        """A credential in a plain string field is shown in the console,
        stored in the deployment's parameters, and read back by anybody
        with access to them."""
        offenders = []

        for name, spec in schema['properties'].items():
            if not name.startswith('secrets.'):
                continue
            marketplace = spec.get('x-google-marketplace') or {}
            if marketplace.get('type') not in ('MASKED_FIELD', 'GENERATED_PASSWORD'):
                offenders.append(name)

        assert not offenders, f'{offenders} are secrets in unmasked fields'


class TestEveryParameterExistsInTheChart:
    """The other direction. A schema property that maps to no chart value
    is a form field that silently does nothing — the customer sets it,
    the deploy succeeds, and the setting was discarded."""

    @pytest.mark.skipif(_HELM is None, reason='helm is not installed')
    def test_every_property_names_a_real_chart_value(self, schema):
        values = yaml.safe_load((_CHART / 'values.yaml').read_text())

        def exists(path: str) -> bool:
            node = values
            for part in path.split('.'):
                if not isinstance(node, dict) or part not in node:
                    return False
                node = node[part]
            return True

        # `name` and `namespace` are marketplace's own, not chart values.
        marketplace_own = {'name', 'namespace'}
        unknown = sorted(
            name for name in _schema_can_supply(schema) if name not in marketplace_own and not exists(name)
        )

        assert not unknown, (
            f'these form fields map to nothing in values.yaml: {unknown}. A customer would '
            'set them, the deploy would succeed, and the answer would be discarded.'
        )


class TestTheApplicationObjectIsOffByDefault:
    """`app.k8s.io` is a CRD. Rendering it into a cluster without the
    Application controller fails the install with `no matches for kind`,
    and the marketplace deployer is what installs that controller."""

    @pytest.mark.skipif(_HELM is None, reason='helm is not installed')
    def test_a_plain_install_renders_none(self):
        assert 'kind: Application' not in _render()

    @pytest.mark.skipif(_HELM is None, reason='helm is not installed')
    def test_the_deployer_gets_one(self):
        rendered = _render('--set', 'marketplace.enabled=true')

        assert 'kind: Application' in rendered

    @pytest.mark.skipif(_HELM is None, reason='helm is not installed')
    def test_it_lists_the_kinds_the_console_should_group(self):
        documents = [d for d in yaml.safe_load_all(_render('--set', 'marketplace.enabled=true')) if d]
        app = next(d for d in documents if d['kind'] == 'Application')

        kinds = {entry['kind'] for entry in app['spec']['componentKinds']}
        assert {'Deployment', 'Service', 'Secret', 'ConfigMap', 'Job'} <= kinds, kinds

    @pytest.mark.skipif(_HELM is None, reason='helm is not installed')
    def test_its_selector_matches_the_workloads(
        self,
    ):
        """A selector that matches nothing renders, installs, and shows
        an empty application in the console."""
        documents = [d for d in yaml.safe_load_all(_render('--set', 'marketplace.enabled=true')) if d]
        app = next(d for d in documents if d['kind'] == 'Application')
        selector = app['spec']['selector']['matchLabels']

        pod_labels = [d['spec']['template']['metadata']['labels'] for d in documents if d['kind'] == 'Deployment']

        assert pod_labels, 'no deployments rendered'
        for labels in pod_labels:
            assert selector.items() <= labels.items(), f'{selector} does not match {labels}'

    @pytest.mark.skipif(_HELM is None, reason='helm is not installed')
    def test_the_optional_kinds_appear_only_when_enabled(self):
        with_backup = _render(
            '--set',
            'marketplace.enabled=true',
            '--set',
            'backup.enabled=true',
            '--set',
            'backup.existingClaim=c',
        )
        without = _render('--set', 'marketplace.enabled=true')

        assert 'CronJob' in with_backup
        assert 'CronJob' not in without


def _render(*extra: str) -> str:
    key = 'DUuX49F9ROzfR-UMMTjm65pV86qN7IyUSzC0gkFQDh0='
    result = subprocess.run(
        [
            _HELM or 'helm',
            'template',
            'igni',
            str(_CHART),
            '--set',
            'image.repository=registry.example.com/igni/igni-api',
            '--set',
            'portalImage.repository=registry.example.com/igni/portal',
            '--set',
            'postgres.host=pg.internal',
            '--set',
            'redis.host=redis.internal',
            '--set',
            f'secrets.secretEncryptionKey={key}',
            '--set',
            'urls.publicBase=https://igni.example.com',
            *extra,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


class TestTheUnverifiedPartIsStated:
    def test_the_schema_says_it_has_not_been_through_mpdev(self):
        """It has not. Saying so in the file is the difference between a
        first draft and something somebody submits believing it was
        checked."""
        text = ' '.join(_SCHEMA.read_text().split())

        assert 'mpdev' in text
        assert 'not' in text and 'verified' in text.lower()
