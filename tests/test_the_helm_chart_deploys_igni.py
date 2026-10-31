"""The Helm chart renders a deployment somebody could actually run.

F98, and the last of §41's two marketplace blockers. Both marketplaces
want a chart: a GCP Kubernetes listing needs one plus a `schema.yaml` of
deployer parameters, and an AWS container listing accepts one in place of
CloudFormation. `docker-compose.prod.yml` is a real working artefact and
is neither.

This file renders the chart with `helm template` and asserts on the
manifests, because a chart is a program and reading one proves very
little. Three classes of bug are covered, and they are the ones that
render perfectly:

* **a workload that starts and does the wrong thing.** A liveness probe
  pointed at `/ready` restarts every replica at once when the database
  blips. A Service selector that does not match its Deployment's labels
  routes to nothing. Both render, lint and validate;
* **a setting with nowhere to go.** `dist.env` is the operator-facing
  contract; a key that exists there and not in the chart is a setting a
  customer cannot set. Asserted as a rule against that file rather than
  as a list here;
* **a value the chart guesses instead of refusing.** Ten inputs must
  refuse, each with a message naming what is missing. Compose learned
  this the hard way: its image was `ember/ember-api:latest`, which is
  unqualified and therefore Docker Hub, a namespace that is not ours.

What this file does *not* prove: that the chart installs. There is no
cluster here, and the manifests are validated against the Kubernetes
schemas rather than accepted by an API server. Stated plainly because the
difference matters — see §49.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

from dotenv import dotenv_values
import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_CHART = _ROOT / 'charts/igni'
_TEMPLATE = _ROOT / 'dist.env'

_HELM = shutil.which('helm')

# A real Fernet key, used nowhere else: the chart and the server both
# refuse without one, and the chart checks its length.
_KEY = 'DUuX49F9ROzfR-UMMTjm65pV86qN7IyUSzC0gkFQDh0='

_MINIMAL = [
    '--set',
    'image.repository=registry.example.com/igni/igni-api',
    '--set',
    'portalImage.repository=registry.example.com/igni/portal',
    '--set',
    'postgres.host=pg.internal',
    '--set',
    'redis.host=redis.internal',
    '--set',
    f'secrets.secretEncryptionKey={_KEY}',
    '--set',
    'urls.publicBase=https://igni.example.com',
]

# Every optional value populated, so the coverage assertions see the whole
# surface rather than the subset a minimal install renders.
_FULL = _MINIMAL + [
    '--set',
    'secrets.postgresPassword=pg-password',
    '--set',
    'secrets.smtpPassword=smtp-password',
    '--set',
    'secrets.githubAppPrivateKey=PEM',
    '--set',
    'secrets.githubWebhookSecret=gh-hook',
    '--set',
    'secrets.gitlabWebhookSecret=gl-hook',
    '--set',
    'secrets.superAdminPasswordHash=bcrypt-hash',
    '--set',
    'secrets.serviceToken=svc-token',
    '--set',
    'secrets.metricsToken=metrics-token',
    '--set',
    'secrets.oidcClientSecret=oidc-secret',
    '--set',
    'secrets.googleClientSecret=google-secret',
    '--set',
    'secrets.scimToken=scim-token',
    '--set',
    'secrets.secretEncryptionKeysPrevious=an-old-fernet-key',
    '--set',
    'sso.issuer=https://idp.example.com',
    '--set',
    'sso.clientId=igni',
    '--set',
    'github.appId=12345',
    '--set',
    'codeindex.baseUrl=http://code-index:8000',
    '--set',
    'urls.portalBase=https://igni.example.com',
    '--set',
    'urls.oauthRedirectBase=https://igni.example.com',
    '--set',
    'urls.oauthFrontendBase=https://igni.example.com',
    '--set',
    'email.enabled=true',
    '--set',
    'email.smtpHost=mail.internal',
    '--set',
    'email.smtpUsername=igni',
    '--set',
    'retention.auditDays=365',
    '--set',
    'retention.webhookEventDays=30',
    '--set',
    'ingress.enabled=true',
    '--set',
    'ingress.host=igni.example.com',
]


# Artifacts, published on its own host. Kept out of `_FULL`: its Service
# is deployed separately, so the "every backend names a Service in this
# release" rule cannot hold for it.
_ARTIFACTS_WITHOUT_HOST = [
    '--set',
    'artifacts.enabled=true',
    '--set',
    'artifacts.serviceName=igni-artifacts-svc',
]
_ARTIFACTS = _ARTIFACTS_WITHOUT_HOST + ['--set', 'artifacts.host=artifacts.example.com']


def _without(*keys: str) -> list[str]:
    """`_MINIMAL` minus the named settings.

    These cases used to be index slices, and the comment below recorded
    what that cost: one of them dropped two required values and so
    asserted which refusal helm happened to report first, passing for a
    year before DEC-13 removed two unrelated templates and changed the
    order. Adding an image would have done it again — every slice past
    the insertion point shifts.

    Naming what is removed cannot shift.
    """
    kept: list[str] = []
    pairs = zip(_MINIMAL[::2], _MINIMAL[1::2], strict=True)
    for flag, setting in pairs:
        if not any(setting.startswith(f'{key}=') for key in keys):
            kept += [flag, setting]
    return kept


pytestmark = pytest.mark.skipif(_HELM is None, reason='helm is not installed')


def _render(*args: str) -> list[dict]:
    result = subprocess.run(
        [_HELM or 'helm', 'template', 'igni', str(_CHART), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f'helm template failed:\n{result.stderr}'
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _refuse(*args: str) -> str:
    """Render and require failure. Returns the message a customer sees."""
    result = subprocess.run(
        [_HELM or 'helm', 'template', 'igni', str(_CHART), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0, 'the chart rendered when it should have refused. Output:\n' + result.stdout[:2000]
    return result.stderr


@pytest.fixture(scope='module')
def manifests() -> list[dict]:
    return _render(*_FULL)


def _of_kind(manifests: list[dict], kind: str) -> list[dict]:
    return [m for m in manifests if m.get('kind') == kind]


def _pod_specs(manifests: list[dict]) -> dict[str, dict]:
    """Every pod template in the release, keyed by workload name."""
    specs = {}
    for m in manifests:
        if m.get('kind') in ('Deployment', 'Job'):
            specs[m['metadata']['name']] = m['spec']['template']['spec']
    return specs


class TestTheChartRendersAtAll:
    """If any of these fail, everything below is measuring nothing."""

    def test_helm_is_available_here(self):
        assert _HELM, 'helm is not installed, so this file skipped rather than ran'

    def test_helm_lint_passes(self):
        result = subprocess.run(
            [_HELM or 'helm', 'lint', str(_CHART), *_MINIMAL],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_it_renders_the_workloads_a_deployment_needs(self, manifests):
        names = {m['metadata']['name'] for m in manifests}

        for expected in (
            'igni-api',
            'igni-portal',
            'igni-migrate',
            'igni-config',
            'igni-secrets',
        ):
            assert expected in names, f'{expected} is missing from the release. Rendered: {sorted(names)}'

    def test_ci_installs_helm_so_this_file_cannot_skip_silently(self):
        """The tests above skip without helm, and a skipped suite reads as
        a passing one. Two sweeps earlier in this review looked like
        success while measuring nothing, so the CI side is asserted here."""
        ci = (_ROOT / '.github/workflows/ci.yml').read_text()

        assert (
            'helm' in ci.lower()
        ), 'CI does not install helm, so every assertion in this file skips there — and a skip is not a pass'


class TestEveryWorkloadIsHardened:
    """The container requirements both marketplaces review against, and
    the ones an image scanner flags. F89 moved the images off root; this
    is the half of that which lives in the pod spec."""

    def test_every_pod_runs_as_a_numeric_non_root_user(self, manifests):
        for name, spec in _pod_specs(manifests).items():
            ctx = spec.get('securityContext', {})
            assert ctx.get('runAsNonRoot') is True, f'{name} does not set runAsNonRoot'
            uid = ctx.get('runAsUser')
            assert isinstance(uid, int) and uid > 0, (
                f'{name} has runAsUser={uid!r}. runAsNonRoot cannot verify a named user '
                'and refuses the pod with "image has non-numeric user".'
            )

    def test_the_portal_declares_the_group_its_nginx_needs(self, manifests):
        """Not cosmetic, and found by running the image rather than
        reading it. The portal's nginx renders a config template into
        /etc/nginx/conf.d at startup; with a read-only root that is an
        emptyDir, owned by root unless fsGroup says otherwise. Without it
        nginx logs "conf.d is not writable", starts anyway with no server
        block, and answers nothing while reporting Running."""
        spec = _pod_specs(manifests)['igni-portal']

        assert spec['securityContext'].get('fsGroup') == 101, (
            'the portal pod does not set fsGroup: 101, so its emptyDir volumes are '
            'root-owned and nginx silently serves nothing'
        )

        mounts = {m['mountPath'] for m in spec['containers'][0]['volumeMounts']}
        assert '/etc/nginx/conf.d' in mounts, mounts
        assert '/var/cache/nginx' in mounts, mounts

    def test_no_container_can_write_its_own_root_filesystem(self, manifests):
        for name, spec in _pod_specs(manifests).items():
            for container in spec['containers']:
                ctx = container.get('securityContext', {})
                assert ctx.get('readOnlyRootFilesystem') is True, f'{name}/{container["name"]}'
                assert ctx.get('allowPrivilegeEscalation') is False, f'{name}/{container["name"]}'
                assert ctx.get('capabilities', {}).get('drop') == ['ALL'], f'{name}/{container["name"]}'

    def test_anything_that_clones_a_repository_has_somewhere_to_write(self, manifests):
        """The API clones into /tmp/repos and /tmp/gitlab-repos — the only
        runtime filesystem writes in `app/`, checked against the source.
        With a read-only root and no volume there, every clone fails."""
        for name in ('igni-api',):
            spec = _pod_specs(manifests)[name]
            mounts = {m['mountPath'] for m in spec['containers'][0]['volumeMounts']}

            assert '/tmp' in mounts, f'{name} has a read-only root and no writable /tmp'

    def test_ephemeral_storage_is_bounded_where_clones_land(self, manifests):
        """An unbounded emptyDir is node disk. A runaway clone fills the
        node and evicts whatever else is scheduled there, including pods
        belonging to somebody else."""
        for name in ('igni-api',):
            spec = _pod_specs(manifests)[name]
            tmp = next(v for v in spec['volumes'] if v['name'] == 'tmp')

            assert tmp['emptyDir'].get('sizeLimit'), f'{name}: /tmp emptyDir has no sizeLimit'

    def test_every_container_declares_what_it_needs(self, manifests):
        """Requests are what the scheduler honours and what a marketplace
        review looks for. A memory limit is what stops one pod taking the
        node down; a CPU limit is deliberately absent, since throttling an
        asyncio process produces latency that looks like a network
        fault."""
        for name, spec in _pod_specs(manifests).items():
            for container in spec['containers']:
                resources = container.get('resources', {})
                assert resources.get('requests', {}).get('cpu'), f'{name}/{container["name"]}: no cpu request'
                assert resources.get('requests', {}).get('memory'), f'{name}/{container["name"]}: no memory request'
                assert resources.get('limits', {}).get('memory'), f'{name}/{container["name"]}: no memory limit'

    def test_no_pod_mounts_a_kubernetes_api_token(self, manifests):
        """Nothing in igni talks to the Kubernetes API, so a mounted token
        is only useful to whoever compromises a container."""
        accounts = _of_kind(manifests, 'ServiceAccount')

        assert accounts, 'no ServiceAccount rendered'
        for account in accounts:
            assert account.get('automountServiceAccountToken') is False, account['metadata']['name']


class TestTheProbesSayWhatTheyMean:
    def test_liveness_asks_health_and_readiness_asks_ready(self, manifests):
        """Swapping these is the classic mistake and it renders perfectly.
        Liveness decides whether to *restart*, and a restart fixes a
        wedged process but not an unreachable database — pointed at
        /ready it turns a database blip into a simultaneous restart loop
        across every replica."""
        container = _pod_specs(manifests)['igni-api']['containers'][0]

        assert (
            container['livenessProbe']['httpGet']['path'] == '/health'
        ), 'the API liveness probe is not /health. If it is /ready, a database blip restarts every replica at once.'
        assert container['readinessProbe']['httpGet']['path'] == '/ready'

    def test_the_migration_job_has_no_probes(self, manifests):
        """A Job that exits 0 is finished, not unhealthy."""
        container = _pod_specs(manifests)['igni-migrate']['containers'][0]

        assert 'livenessProbe' not in container
        assert 'readinessProbe' not in container


class TestTheMigrationRunsBeforeAnythingServes:
    def test_it_is_a_pre_install_and_pre_upgrade_hook(self, manifests):
        job = next(m for m in _of_kind(manifests, 'Job') if m['metadata']['name'] == 'igni-migrate')
        annotations = job['metadata']['annotations']

        assert annotations['helm.sh/hook'] == 'pre-install,pre-upgrade'

    def test_a_failed_migration_leaves_its_logs_behind(self, manifests):
        """The first question after a failed upgrade is what the migration
        said. `hook-succeeded` would delete exactly that."""
        job = next(m for m in _of_kind(manifests, 'Job') if m['metadata']['name'] == 'igni-migrate')

        assert job['metadata']['annotations']['helm.sh/hook-delete-policy'] == 'before-hook-creation'

    def test_the_hook_does_not_read_objects_that_do_not_exist_yet(self, manifests):
        """The bug this nearly shipped with. Helm hooks run *before* the
        release's own resources are created, so a pre-install Job reading
        the main ConfigMap fails with CreateContainerConfigError on a
        first install — which reads as a configuration mistake rather
        than an ordering one. Its env comes from hook-scoped copies."""
        spec = _pod_specs(manifests)['igni-migrate']
        referenced = set()
        for source in spec['containers'][0].get('envFrom', []):
            for key in ('configMapRef', 'secretRef'):
                if key in source:
                    referenced.add(source[key]['name'])

        assert referenced, 'the migration job takes no configuration at all'
        assert (
            'igni-config' not in referenced
        ), 'the migration hook reads igni-config, which does not exist when a hook runs on a first install'
        assert (
            'igni-secrets' not in referenced
        ), 'the migration hook reads igni-secrets, which does not exist when a hook runs'

    def test_everything_the_hook_names_is_itself_a_hook_that_sorts_first(self, manifests):
        """A rule over the pod spec, because the list version missed one.

        This used to name `igni-migrate-env` and check that one object.
        The Job also names a **ServiceAccount**, which is an ordinary
        release resource — created after every pre-install hook — so
        Kubernetes refused to create the pod at all:

            Error creating: pods "igni-migrate-" is forbidden: error
            looking up service account igni/igni: serviceaccount "igni"
            not found

        `helm install` therefore failed on every cluster, always. Found
        by installing on kind (F1), not by rendering: hook ordering is a
        property of the *set* of manifests, and rendering, linting and
        schema validation each look at one object at a time.

        So: whatever the hook references — env sources, the service
        account, mounted volumes — must be in the release *and* be a hook
        *and* sort before it. Anything new the Job starts naming is
        covered the day it is added.
        """
        job = next(m for m in _of_kind(manifests, 'Job') if m['metadata']['name'] == 'igni-migrate')
        job_weight = int(job['metadata']['annotations']['helm.sh/hook-weight'])
        spec = _pod_specs(manifests)['igni-migrate']

        referenced: set[str] = set()
        for source in spec['containers'][0].get('envFrom', []):
            for key in ('configMapRef', 'secretRef'):
                if key in source:
                    referenced.add(source[key]['name'])
        if 'serviceAccountName' in spec:
            referenced.add(spec['serviceAccountName'])
        for volume in spec.get('volumes', []):
            for key in ('configMap', 'secret'):
                if key in volume:
                    referenced.add(volume[key].get('name') or volume[key].get('secretName'))

        assert referenced, 'the migration job references nothing at all, so this proves nothing'

        # Keyed by name *and* kind: a Job and a ServiceAccount may share a
        # name, and keying on name alone had this rule comparing the Job
        # against itself. (Which it caught, on its first run.)
        by_name: dict[str, dict] = {}
        for m in manifests:
            if m.get('kind') != 'Job':
                by_name[m['metadata']['name']] = m
        problems = []
        for name in sorted(referenced):
            obj = by_name.get(name)
            if obj is None:
                problems.append(f'{name}: named by the hook and not in the release')
                continue
            annotations = obj['metadata'].get('annotations') or {}
            hook = annotations.get('helm.sh/hook')
            if hook is None:
                problems.append(f'{name}: an ordinary resource, so Helm creates it *after* this hook runs')
                continue
            if 'pre-install' not in hook:
                problems.append(f'{name}: hook is {hook!r}, which does not include pre-install')
                continue
            weight = int(annotations.get('helm.sh/hook-weight', 0))
            if weight >= job_weight:
                problems.append(f'{name}: weight {weight} does not sort before the job at {job_weight}')

        assert not problems, 'the migration hook names things that will not exist when it runs:\n  ' + '\n  '.join(
            problems
        )

    def test_the_hook_carries_only_what_alembic_reads(self, manifests):
        """`alembic/env.py` reads `settings.postgres_url_sync` and
        `settings.app_env`. No signing key, no encryption key, no
        provider credentials — so the hook-scoped copy holds one password
        and five coordinates rather than a second copy of everything."""
        config = next(m for m in _of_kind(manifests, 'ConfigMap') if m['metadata']['name'] == 'igni-migrate-env')

        assert set(config['data']) <= {
            'APP_ENV',
            'LOG_LEVEL',
            'POSTGRES_HOST',
            'POSTGRES_PORT',
            'POSTGRES_USER',
            'POSTGRES_DB',
        }, config['data']

        secrets = [m for m in _of_kind(manifests, 'Secret') if m['metadata']['name'] == 'igni-migrate-env']
        if secrets:
            assert set(secrets[0]['stringData']) == {'POSTGRES_PASSWORD'}, secrets[0]['stringData']


class TestKubernetesDoesNotGetToNameOurSettings:
    """The second thing installing on kind found, and the worse one.

    Kubernetes injects an environment variable per Service in the
    namespace. The chart tells you to bring your own Postgres and Redis
    (`values.yaml`, "The data stores, both external"), so the obvious
    thing a customer does is deploy a Service called `redis` next to it —
    and Kubernetes then sets, in every pod in that namespace:

        REDIS_PORT=tcp://10.96.112.222:6379

    which lands on `Settings.redis_port: int` and kills the process
    before it starts:

        ValidationError: 1 validation error for Settings
        redis_port
          Input should be a valid integer, unable to parse string as an
          integer [input_value='tcp://10.96.112.222:6379']

    Every pod, not just the migration: the API and the portal are both in
    that namespace. A Service named `postgres` does the same
    thing to `postgres_port`.

    Nothing in igni discovers anything through those variables — every
    host and port comes from configuration — so the links are pure
    hazard. Off everywhere, and this is the rule rather than four
    separate assertions, so a workload added tomorrow is covered.
    """

    def test_no_pod_template_gets_service_links(self):
        """Every workload the chart can produce, including whatever
        defaults off — a rule that only checked the enabled ones would
        pass while the backup CronJob kept the bug."""
        manifests = _render(
            *_FULL,
            '--set',
            'backup.enabled=true',
            '--set',
            'backup.existingClaim=igni-backups',
        )

        templates: dict[str, dict] = {}
        for m in manifests:
            kind = m.get('kind')
            name = m['metadata']['name']
            if kind in ('Deployment', 'Job'):
                templates[f'{kind}/{name}'] = m['spec']['template']['spec']
            elif kind == 'CronJob':
                templates[f'{kind}/{name}'] = m['spec']['jobTemplate']['spec']['template']['spec']

        # Derived, not counted. This read `>= 6` and went stale the day
        # DEC-13 deleted two templates — a guard whose whole job is to
        # prove the render measured something, failing because the render
        # correctly measured less. The chart's own template directory is
        # the only honest source for how many there should be.
        defining = {path.name for path in (_CHART / 'templates').glob('*.yaml') if 'containers:' in path.read_text()}
        assert defining, 'found no pod-defining templates, so this rule proves nothing'
        assert len(templates) == len(defining), (
            f'{len(defining)} templates define containers ({sorted(defining)}) but the render '
            f'produced {sorted(templates)}. Either a workload is gated off in these values — '
            'in which case enable it here — or the parse above is wrong.'
        )

        leaking = sorted(name for name, spec in templates.items() if spec.get('enableServiceLinks') is not False)
        assert not leaking, (
            'these pods let Kubernetes inject a variable per Service in the namespace, '
            f'which collides with the settings model: {leaking}'
        )


class TestNothingIsRoutedToNothing:
    """Both directions, and the second one was learned the hard way.

    This class checked that every Service selects a real pod. It did not
    check that every pod is *reachable* — and while removing Flower from
    `services.yaml`, the block-removal took the **portal Service** with
    it, because the portal's definition sat immediately above the
    `{{- if .Values.flower.enabled }}` guard.

    Nothing here failed. `test_it_renders_the_workloads_a_deployment_needs`
    passed because `igni-portal` still matched the *Deployment*. The
    Ingress test passed because it asserted the backend was **named**
    `igni-portal`, not that anything by that name existed. The chart
    rendered, `helm install` succeeded, both Deployments rolled out — and
    the portal was unreachable, which only `tools/kind/install.sh` found,
    by asking for the page.

    That is F1's lesson again: rendering is not installing. The two rules
    below are what would have caught it without a cluster.
    """

    def test_every_deployment_is_reachable_through_a_service(self, manifests):
        """The completeness direction (F145). A Deployment nothing selects is a
        pod running correctly with no way in, which is invisible to
        everything except a request."""
        services = _of_kind(manifests, 'Service')
        assert services, 'no Services at all in the release'

        for deployment in _of_kind(manifests, 'Deployment'):
            labels = deployment['spec']['template']['metadata']['labels']
            selecting = [
                svc['metadata']['name']
                for svc in services
                if svc['spec'].get('selector') and svc['spec']['selector'].items() <= labels.items()
            ]

            assert selecting, (
                f'Deployment {deployment["metadata"]["name"]} is selected by no Service in '
                'this release. Its pods will run and nothing can reach them.'
            )

    def test_every_ingress_backend_names_a_service_that_exists(self, manifests):
        """F145. A backend naming an absent Service is a 503 from the ingress
        controller. The route table below asserts *which* Service each
        path goes to; this asserts the Service is there to go to."""
        existing = {svc['metadata']['name'] for svc in _of_kind(manifests, 'Service')}
        ingresses = _of_kind(manifests, 'Ingress')
        assert ingresses, 'the full values enable the Ingress, so this should have rendered one'

        for ingress in ingresses:
            for rule in ingress['spec']['rules']:
                for path in rule['http']['paths']:
                    backend = path['backend']['service']['name']

                    assert backend in existing, (
                        f'{ingress["metadata"]["name"]} routes {path["path"]} to Service '
                        f'{backend}, which this release does not create. Rendered: '
                        f'{sorted(existing)}'
                    )

    def test_every_service_selector_matches_a_deployment(self, manifests):
        """A selector that matches no pod renders, lints, validates and
        serves 503s. Checked by matching each Service's selector against
        the pod labels the Deployments actually produce."""
        pod_labels = [m['spec']['template']['metadata']['labels'] for m in _of_kind(manifests, 'Deployment')]

        for service in _of_kind(manifests, 'Service'):
            selector = service['spec']['selector']
            matched = [labels for labels in pod_labels if selector.items() <= labels.items()]

            assert matched, (
                f'Service {service["metadata"]["name"]} selects {selector}, which matches no '
                'pod template in this release — it would answer nothing'
            )

    def test_the_ingress_sends_the_api_and_the_browser_to_different_places(self, manifests):
        ingress = _of_kind(manifests, 'Ingress')[0]
        paths = ingress['spec']['rules'][0]['http']['paths']
        routes = {p['path']: p['backend']['service']['name'] for p in paths}

        assert routes['/v1'] == 'igni-api', routes
        assert routes['/'] == 'igni-portal', routes

    def test_the_public_keys_are_served_by_the_api(self, manifests):
        """Services fetch igni's signing keys from
        `PUBLIC_BASE_URL/.well-known/jwks.json`. Under `/` they would reach
        the portal and get its index page instead of a key set."""
        ingress = _of_kind(manifests, 'Ingress')[0]
        paths = ingress['spec']['rules'][0]['http']['paths']
        routes = {p['path']: p['backend']['service']['name'] for p in paths}

        assert routes.get('/.well-known') == 'igni-api', routes

    def test_the_api_documentation_is_not_published(self, manifests):
        """`/docs`, `/redoc` and `/openapi.json` are served to anything
        inside the cluster. Publishing them on the internet is a separate
        decision, and not this chart's to make."""
        ingress = _of_kind(manifests, 'Ingress')[0]
        paths = {p['path'] for p in ingress['spec']['rules'][0]['http']['paths']}

        assert not {'/docs', '/redoc', '/openapi.json'} & paths, paths

    def test_the_portal_is_pointed_at_the_api_service(self, manifests):
        """nginx resolves its upstream at startup and exits if it does not
        resolve, so this name has to be the Service's."""
        container = _pod_specs(manifests)['igni-portal']['containers'][0]
        upstream = next(e['value'] for e in container['env'] if e['name'] == 'API_UPSTREAM')

        assert upstream == 'igni-api:7777', upstream
        assert 'igni-api' in {m['metadata']['name'] for m in _of_kind(manifests, 'Service')}


@pytest.fixture(scope='module')
def with_artifacts() -> list[dict]:
    return _render(*_FULL, *_ARTIFACTS)


class TestAServiceHasItsOwnHost:
    """Artifacts is served on its own hostname, never as a path on igni's.

    A service's URL is its identity (docs/service-identity.md): the
    audience of every token people get for it, and the base igni calls
    for erasure and export. It used to be `/v1/artifacts` on igni's host,
    which shared igni's origin — a token's audience could no longer say
    which service it was for — and took a path igni could never use.
    """

    @staticmethod
    def _artifacts_env(manifests: list[dict]) -> dict:
        return next(m for m in _of_kind(manifests, 'ConfigMap') if m['metadata']['name'] == 'igni-artifacts-env')[
            'data'
        ]

    def test_no_path_on_igni_host_routes_to_artifacts(self, with_artifacts):
        igni_rules = [
            rule
            for ingress in _of_kind(with_artifacts, 'Ingress')
            for rule in ingress['spec']['rules']
            if rule['host'] == 'igni.example.com'
        ]
        assert igni_rules, 'no rule for igni.example.com rendered at all'

        for rule in igni_rules:
            for path in rule['http']['paths']:
                backend = path['backend']['service']['name']
                assert backend != 'igni-artifacts-svc', f'{path["path"]} on igni\'s host routes to Artifacts'
                assert not path['path'].startswith('/v1/artifacts'), path

    def test_artifacts_is_served_on_its_own_host(self, with_artifacts):
        rules = [
            rule
            for ingress in _of_kind(with_artifacts, 'Ingress')
            for rule in ingress['spec']['rules']
            if rule['host'] == 'artifacts.example.com'
        ]

        assert len(rules) == 1, rules
        routes = {p['path']: p['backend']['service'] for p in rules[0]['http']['paths']}
        assert routes == {'/': {'name': 'igni-artifacts-svc', 'port': {'number': 8100}}}, routes

    def test_its_public_base_url_is_its_own_host(self, with_artifacts):
        """The address it is deployed with is the one the Ingress
        publishes, by construction — and not igni's."""
        env = self._artifacts_env(with_artifacts)
        published = {
            rule['host']
            for ingress in _of_kind(with_artifacts, 'Ingress')
            if ingress['metadata']['name'] == 'igni-artifacts'
            for rule in ingress['spec']['rules']
        }
        igni_public = _of_kind(with_artifacts, 'ConfigMap')
        igni_public = next(m for m in igni_public if m['metadata']['name'] == 'igni-config')['data']['PUBLIC_BASE_URL']

        assert env['PUBLIC_BASE_URL'] == 'https://artifacts.example.com', env
        assert published == {'artifacts.example.com'}, published
        assert env['PUBLIC_BASE_URL'] != igni_public

    def test_it_reaches_igni_through_the_api_service(self, with_artifacts):
        env = self._artifacts_env(with_artifacts)
        services = {svc['metadata']['name'] for svc in _of_kind(with_artifacts, 'Service')}

        assert env['IGNI_SERVER_BASE_URL'] == 'http://igni-api.default.svc:7777', env
        assert 'igni-api' in services

    def test_its_certificate_is_its_own(self):
        manifests = _render(
            *_FULL,
            *_ARTIFACTS,
            '--set',
            'ingress.tls.enabled=true',
            '--set',
            'ingress.tls.secretName=igni-tls',
            '--set',
            'artifacts.tls.secretName=artifacts-tls',
        )
        tls = {i['metadata']['name']: i['spec']['tls'] for i in _of_kind(manifests, 'Ingress')}

        assert tls['igni'] == [{'hosts': ['igni.example.com'], 'secretName': 'igni-tls'}], tls
        assert tls['igni-artifacts'] == [{'hosts': ['artifacts.example.com'], 'secretName': 'artifacts-tls'}], tls

    def test_disabled_renders_nothing_for_it(self, manifests):
        names = {m['metadata']['name'] for m in manifests}

        assert 'igni-artifacts' not in names
        assert 'igni-artifacts-env' not in names


class TestConfigurationChangesReachTheRunningPods:
    def test_a_config_change_rolls_the_pods(self, manifests):
        """Without a checksum annotation, an upgrade that alters only a
        ConfigMap value changes nothing in the pod spec — so Kubernetes
        has no reason to restart anything, the old settings stay live, and
        the upgrade reports success having done nothing.

        Every Deployment that reads the ConfigMap, derived rather than
        listed. It was `('igni-api', 'igni-worker')`, which is the shape
        that needs editing whenever a workload appears or leaves — and
        the portal is deliberately not in it, because it reads no
        application config at all."""
        readers = [
            deployment
            for deployment in _of_kind(manifests, 'Deployment')
            if any(
                source.get('configMapRef', {}).get('name') == 'igni-config'
                for container in deployment['spec']['template']['spec']['containers']
                for source in container.get('envFrom', [])
            )
        ]
        assert readers, 'no Deployment reads igni-config, so this rule is measuring nothing'

        for deployment in readers:
            name = deployment['metadata']['name']
            annotations = deployment['spec']['template']['metadata'].get('annotations') or {}

            assert 'checksum/config' in annotations, f'{name} does not roll on a config change'

    def test_the_checksum_actually_changes_with_the_config(self):
        """A checksum computed over the wrong thing is a constant, and a
        constant annotation is no annotation at all."""
        before = _render(*_FULL)
        after = _render(*_FULL, '--set', 'logLevel=DEBUG')

        def checksum(manifests: list[dict]) -> str:
            api = next(m for m in _of_kind(manifests, 'Deployment') if m['metadata']['name'] == 'igni-api')
            return api['spec']['template']['metadata']['annotations']['checksum/config']

        assert checksum(before) != checksum(after)

    def test_the_selector_does_not_move_when_the_release_changes(self, manifests):
        """A Deployment's selector is immutable after creation. If it
        carried the chart version or the image tag, the next upgrade would
        fail with "field is immutable" and need the Deployment deleted by
        hand."""
        for deployment in _of_kind(manifests, 'Deployment'):
            selector = deployment['spec']['selector']['matchLabels']

            assert 'helm.sh/chart' not in selector, deployment['metadata']['name']
            assert 'app.kubernetes.io/version' not in selector, deployment['metadata']['name']


class TestAPodGetsTimeToFinishWhatItStarted:
    """The two numbers that have to be read together, in different files.

    `ai.upstreamTimeout` says the API will wait five minutes for a model
    to answer. Kubernetes defaults `terminationGracePeriodSeconds` to
    **30 seconds**, and nothing set it — so every rolling upgrade
    SIGKILLed any completion still streaming after half a minute. For an
    AI coding agent a long completion is the ordinary case, not the edge:
    the developer sees the stream stop mid-sentence, and the concurrency
    lease it held sits in Redis for the full 15-minute lease timeout,
    making that GPU look busier than it is.

    Asserted as a *relationship* rather than a value, because the failure
    is the two drifting apart — somebody raising the request budget and
    not the shutdown budget. F119.
    """

    def test_the_api_is_given_at_least_as_long_as_it_will_wait_for_a_model(self, manifests):
        api = next(m for m in _of_kind(manifests, 'Deployment') if m['metadata']['name'] == 'igni-api')
        grace = api['spec']['template']['spec'].get('terminationGracePeriodSeconds')

        assert grace is not None, (
            'the API deployment sets no terminationGracePeriodSeconds, so Kubernetes uses 30 '
            'seconds — less than a tenth of the time this API waits for a single model response'
        )

        config = next(m for m in _of_kind(manifests, 'ConfigMap') if m['metadata']['name'] == 'igni-config')
        upstream = float(config['data']['AI__UPSTREAM_TIMEOUT'])

        assert grace >= upstream, (
            f'the API waits {upstream:.0f}s for a model but is given {grace}s to shut down, so a '
            f'rolling upgrade kills any response still streaming after {grace}s. Raise '
            'api.terminationGracePeriodSeconds with ai.upstreamTimeout.'
        )

    def test_raising_the_timeout_raises_the_grace_period_with_it(self):
        """The two cannot drift, because one is derived from the other.

        The first version of this test asserted that raising the timeout
        alone left the grace period behind — which documented the hazard
        rather than preventing it. Deriving the value in the template is
        strictly better than a test comparing two numbers a person has to
        keep in step.
        """
        rendered = _render(*_FULL, '--set', 'ai.upstreamTimeout=900')
        api = next(m for m in _of_kind(rendered, 'Deployment') if m['metadata']['name'] == 'igni-api')

        assert api['spec']['template']['spec']['terminationGracePeriodSeconds'] >= 900

    def test_an_explicit_value_still_wins(self):
        """Derived is the default, not a rule — an operator who wants a
        faster rollout and accepts the cost may say so."""
        rendered = _render(*_FULL, '--set', 'api.terminationGracePeriodSeconds=60')
        api = next(m for m in _of_kind(rendered, 'Deployment') if m['metadata']['name'] == 'igni-api')

        assert api['spec']['template']['spec']['terminationGracePeriodSeconds'] == 60


class TestTheConnectionBudgetLivesInOnePlace:
    """`DB_POOL_SIZE + DB_MAX_OVERFLOW` is a process's ceiling on
    concurrent queries (F116), and there is now one process that has one.

    This class used to assert the opposite: that the worker *overrode*
    those two keys, because 200 coroutines cannot share a pool sized for
    a web replica, and because an override that drifted into the
    ConfigMap would have made the worker silently inherit the API's
    budget with tasks failing under load as the only symptom.

    DEC-13 removed the worker, so the override went with it — and what is
    left is the simpler rule that was always underneath: exactly one
    place sets these numbers. Two places to change one number is how they
    end up disagreeing.
    """

    def test_the_api_reads_it_from_the_configmap_and_carries_no_copy(self, manifests):
        api = next(m for m in _of_kind(manifests, 'Deployment') if m['metadata']['name'] == 'igni-api')
        container = api['spec']['template']['spec']['containers'][0]
        env = {entry['name'] for entry in container.get('env', [])}

        assert 'DB_POOL_SIZE' not in env, 'the API pool size is set in two places'
        assert 'DB_MAX_OVERFLOW' not in env, 'the API overflow is set in two places'

        config = next(m for m in _of_kind(manifests, 'ConfigMap') if m['metadata']['name'] == 'igni-config')
        assert config['data'].get('DB_POOL_SIZE'), 'nothing sets the pool size at all'
        assert config['data'].get('DB_MAX_OVERFLOW'), 'nothing sets the overflow at all'

    def test_no_workload_overrides_the_shared_budget(self, manifests):
        """The completeness direction, and the one that would catch a
        reintroduction: any pod that sets these in `env` is claiming a
        budget of its own, which is the arrangement this replaced. A rule
        over every workload rather than a check on the two that exist
        today."""
        offenders = {}
        for name, spec in _pod_specs(manifests).items():
            for container in spec['containers']:
                overrides = {
                    e['name'] for e in container.get('env') or [] if e['name'] in {'DB_POOL_SIZE', 'DB_MAX_OVERFLOW'}
                }
                if overrides:
                    offenders[f'{name}/{container["name"]}'] = sorted(overrides)

        assert not offenders, (
            f'these workloads carry their own connection budget: {offenders}. If that is '
            'deliberate, the reason belongs in this test rather than only in the template.'
        )


class TestEverySettingHasAHome:
    """`dist.env` is the operator-facing contract. A key there that the
    chart cannot set is a setting a Kubernetes customer does not have —
    and the list of them is exactly what nobody notices."""

    def test_every_key_in_dist_env_is_settable_through_the_chart(self, manifests):
        expected = set(dotenv_values(_TEMPLATE))

        available: set[str] = set()
        for m in manifests:
            if m['kind'] == 'ConfigMap':
                available |= set(m.get('data') or {})
            if m['kind'] == 'Secret':
                available |= set(m.get('stringData') or {})
        for spec in _pod_specs(manifests).values():
            for container in spec['containers']:
                available |= {e['name'] for e in container.get('env') or []}

        missing = sorted(expected - available)

        assert not missing, (
            f'these settings exist in dist.env and cannot be set through the chart: '
            f'{missing}. A Kubernetes customer would have no way to configure them.'
        )

    def test_the_secrets_are_not_in_the_configmap(self, manifests):
        """A ConfigMap is readable by anything with `get configmaps` and
        shows up in `kubectl describe`. A credential in there is a
        credential in the cluster's least-protected object.

        The vocabulary comes from the Secret template rather than from a
        list of scary words. The first version of this looked for `AUTH`
        as a substring and flagged `OAUTH_REDIRECT_BASE_URL`, which is a
        URL — a rule that cries wolf gets deleted by the next person.
        """
        secret_keys = set(re.findall(r'^\s{2}([A-Z][A-Z0-9_]+):', (_CHART / 'templates/secret.yaml').read_text(), re.M))
        assert len(secret_keys) >= 8, f'only found {secret_keys} in the Secret template — the parse is wrong'

        config = next(m for m in _of_kind(manifests, 'ConfigMap') if m['metadata']['name'] == 'igni-config')
        leaked = sorted(secret_keys & set(config['data']))

        assert not leaked, f'{leaked} belong in the Secret and are in the ConfigMap'

    def test_an_existing_secret_replaces_the_chart_created_one(self, manifests):
        """The better arrangement, and it has to actually work: with
        `existingSecret` set, the chart must create no Secret of its own
        and every workload must read the customer's."""
        rendered = _render(*_MINIMAL, '--set', 'secrets.existingSecret=my-own-secret')

        assert not _of_kind(rendered, 'Secret'), 'the chart still created a Secret of its own'

        # The portal is deliberately absent from this: it holds no
        # credential at all, only the upstream address of the API.
        for name in ('igni-api', 'igni-migrate'):
            spec = _pod_specs(rendered)[name]
            refs = {
                source['secretRef']['name']
                for source in spec['containers'][0].get('envFrom', [])
                if 'secretRef' in source
            }
            assert refs == {'my-own-secret'}, f'{name} reads {refs}'

        portal = _pod_specs(rendered)['igni-portal']['containers'][0]
        assert not portal.get('envFrom'), 'the portal should hold no credential'


class TestItRefusesRatherThanGuessing:
    """Every input below must fail with a message naming what is wrong. A
    chart that renders something plausible from nothing is the failure
    mode compose already had."""

    @pytest.mark.parametrize(
        'expected_phrase,args',
        [
            # Each case removes exactly one required value, by name —
            # see `_without`. Removing two makes the assertion about
            # which refusal helm reports first, which is not a property
            # of the chart.
            ('image.repository is required', _without('image.repository')),
            ('must not be `latest`', _MINIMAL + ['--set', 'image.tag=latest']),
            ('portalImage.repository is required', _without('portalImage.repository')),
            ('postgres.host is required', _without('postgres.host')),
            ('redis.host is required', _without('redis.host')),
            ('secrets.secretEncryptionKey is required', _without('secrets.secretEncryptionKey')),
            (
                'is not a Fernet key',
                _without('secrets.secretEncryptionKey') + ['--set', 'secrets.secretEncryptionKey=short'],
            ),
            ('urls.publicBase is required', _without('urls.publicBase')),
            (
                'urls.publicBase is required',
                _without('urls.publicBase') + ['--set', 'urls.publicBase=http://igni.example.com'],
            ),
            ('ingress.host is required', _MINIMAL + ['--set', 'ingress.enabled=true']),
            (
                'ingress.tls.secretName is required',
                _MINIMAL
                + [
                    '--set',
                    'ingress.enabled=true',
                    '--set',
                    'ingress.host=h.example.com',
                    '--set',
                    'ingress.tls.enabled=true',
                ],
            ),
            ('artifacts.host is required', _MINIMAL + _ARTIFACTS_WITHOUT_HOST),
            (
                'artifacts.host must differ from ingress.host',
                _MINIMAL
                + _ARTIFACTS_WITHOUT_HOST
                + ['--set', 'ingress.enabled=true', '--set', 'ingress.host=igni.example.com']
                + ['--set', 'artifacts.host=igni.example.com'],
            ),
            (
                'artifacts.host must be a bare hostname',
                _MINIMAL + _ARTIFACTS_WITHOUT_HOST + ['--set', 'artifacts.host=https://artifacts.example.com'],
            ),
            (
                'artifacts.serviceName is required',
                _MINIMAL + ['--set', 'artifacts.enabled=true', '--set', 'artifacts.host=a.example.com'],
            ),
            (
                'artifacts.tls.secretName is required',
                _MINIMAL
                + _ARTIFACTS
                + [
                    '--set',
                    'ingress.enabled=true',
                    '--set',
                    'ingress.host=igni.example.com',
                    '--set',
                    'ingress.tls.enabled=true',
                    '--set',
                    'ingress.tls.secretName=igni-tls',
                ],
            ),
        ],
        ids=[
            'no-api-image',
            'latest-tag',
            'no-portal-image',
            'no-postgres',
            'no-redis',
            'no-encryption-key',
            'not-a-fernet-key',
            'no-public-url',
            'plain-http-public-url',
            'ingress-without-host',
            'tls-without-secret',
            'artifacts-without-host',
            'artifacts-on-igni-host',
            'artifacts-host-is-a-url',
            'artifacts-without-service',
            'artifacts-tls-without-its-own-secret',
        ],
    )
    def test_it_refuses(self, expected_phrase, args):
        message = _refuse(*args)

        assert expected_phrase in message, f'refused, but not for the stated reason:\n{message}'

    def test_the_values_schema_is_enforced(self):
        """`values.schema.json` is only worth having if Helm applies it.
        A typo'd enum should be a named error, not an ignored key.

        Asserted on what the message *says*, not on how Helm words it.
        The two differ, and the old assertion was written against one of
        them:

            3.16.2  - appEnv: appEnv must be one of the following: "prod", "dev"
            4.1.3   - at '/appEnv': value must be one of 'prod', 'dev'

        It matched 4.x, which is what a developer has, while CI pins
        3.16.2 — so this failed only in CI, for a difference in a third
        party's prose rather than anything about this chart. Both
        messages were captured from the real binaries rather than
        guessed; the quotes around the values differ too, so the
        comparison strips them.

        What has to hold either way: the schema is what refused, the
        offending key is named, and the allowed values are listed. A
        test that pinned Helm's exact sentence would break again on the
        next release and would be saying nothing more than this does.
        """
        message = _refuse(*_MINIMAL, '--set', 'appEnv=staging')
        unquoted = message.replace('"', '').replace("'", '')

        assert 'specifications of the schema' in message, f'refused, but not because of the schema:\n{message}'
        assert 'appEnv' in message, f'the schema refused without naming the key:\n{message}'
        for allowed in ('prod', 'dev'):
            assert allowed in unquoted, f'the error does not say {allowed} is allowed:\n{message}'

    def test_the_schema_refusal_reads_the_same_on_the_helm_ci_pins(self):
        """The assertion above, against the version CI actually uses.

        `azure/setup-helm` installs 3.16.2 there and a developer has
        whatever is on their machine, so the rule gets checked against
        both or it is only checked against one. Skipped rather than
        failed when Docker is unavailable — this is a cross-version
        guard, not a reason to block somebody without a daemon.
        """
        probe = subprocess.run(
            [
                'docker',
                'run',
                '--rm',
                '-v',
                f'{_CHART.parent}:/charts',
                'alpine/helm:3.16.2',
                'template',
                'igni',
                f'/charts/{_CHART.name}',
                *[a for a in _MINIMAL],
                '--set',
                'appEnv=staging',
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if probe.returncode != 0 and 'docker' in probe.stderr.lower() and 'daemon' in probe.stderr.lower():
            pytest.skip('no Docker daemon; the pinned-Helm cross-check needs one')
        if probe.returncode == 0:
            pytest.skip('the pinned Helm image did not run; nothing to compare')

        message = probe.stderr or probe.stdout
        unquoted = message.replace('"', '').replace("'", '')

        assert 'specifications of the schema' in message, message
        assert 'appEnv' in message, message
        assert all(allowed in unquoted for allowed in ('prod', 'dev')), message

    def test_the_schema_is_valid_json_schema(self):
        schema = json.loads((_CHART / 'values.schema.json').read_text())

        assert schema['$schema'].startswith('https://json-schema.org/')
        assert 'appEnv' in schema['properties']


class TestTheManifestsAreValidKubernetes:
    def test_they_validate_against_the_api_schemas(self, manifests, tmp_path):
        """`kubeconform` where it is installed. Structural validity is
        weaker than an API server accepting them — there is no cluster
        here, and §49 says so — but it does catch a misspelled field,
        which renders and lints happily."""
        kubeconform = shutil.which('kubeconform')
        if not kubeconform:
            pytest.skip('kubeconform is not installed')

        path = tmp_path / 'rendered.yaml'
        path.write_text('\n---\n'.join(yaml.safe_dump(m) for m in manifests))

        result = subprocess.run(
            [kubeconform, '-strict', '-summary', '-kubernetes-version', '1.29.0', str(path)],
            capture_output=True,
            text=True,
            timeout=180,
        )

        assert result.returncode == 0, result.stdout + result.stderr

    def test_every_object_carries_the_labels_that_identify_it(self, manifests):
        """`kubectl delete -l app.kubernetes.io/instance=<release>` has to
        find all of it, and a support conversation starts with somebody
        listing what a release owns."""
        for m in manifests:
            labels = m['metadata'].get('labels', {})

            assert labels.get('app.kubernetes.io/instance') == 'igni', m['metadata']['name']
            assert labels.get('app.kubernetes.io/managed-by') == 'Helm', m['metadata']['name']
            assert labels.get('app.kubernetes.io/part-of') == 'igni', m['metadata']['name']


class TestBackupsAreOffUntilYouChooseWhereTheyGo:
    """F100. The chart runs the same verified `pg_dump` the compose stack
    does — but only once somebody has decided which volume it writes to,
    because the wrong answer looks identical to the right one until a
    recovery."""

    def test_it_is_off_by_default(self, manifests):
        assert not [
            m for m in manifests if m['kind'] == 'CronJob'
        ], 'a backup CronJob renders by default, which means the chart chose your storage for you'

    def test_enabling_it_without_a_claim_refuses(self):
        message = _refuse(*_MINIMAL, '--set', 'backup.enabled=true')

        assert 'backup.existingClaim is required' in message, message

    def test_with_a_claim_it_renders_a_cronjob_and_the_script(self):
        rendered = _render(*_MINIMAL, '--set', 'backup.enabled=true', '--set', 'backup.existingClaim=igni-backups')
        names = {f'{m["kind"]}/{m["metadata"]["name"]}' for m in rendered}

        assert 'CronJob/igni-backup' in names, names
        assert 'ConfigMap/igni-backup-script' in names, names

    def test_the_schedule_is_pinned_to_utc(self):
        """A CronJob without `timeZone` follows the controller's zone,
        which runs twice on one day a year and not at all on another."""
        rendered = _render(*_MINIMAL, '--set', 'backup.enabled=true', '--set', 'backup.existingClaim=c')
        cron = next(m for m in rendered if m['kind'] == 'CronJob')

        assert cron['spec']['timeZone'] == 'Etc/UTC', cron['spec']
        assert (
            cron['spec']['concurrencyPolicy'] == 'Forbid'
        ), 'two dumps of the same database at once is how a backup volume fills up'

    def test_it_runs_once_rather_than_looping(self):
        """The same script, invoked differently: the compose stack loops
        because it has no scheduler, and a CronJob is the scheduler."""
        rendered = _render(*_MINIMAL, '--set', 'backup.enabled=true', '--set', 'backup.existingClaim=c')
        cron = next(m for m in rendered if m['kind'] == 'CronJob')
        container = cron['spec']['jobTemplate']['spec']['template']['spec']['containers'][0]

        assert container['command'][-1] == 'once', container['command']

    def test_it_uses_the_databases_own_image(self):
        """`pg_dump` must be at least the server's version. Pinning it to
        the application image would leave a `pg_dump` behind on the next
        Postgres upgrade."""
        rendered = _render(*_MINIMAL, '--set', 'backup.enabled=true', '--set', 'backup.existingClaim=c')
        cron = next(m for m in rendered if m['kind'] == 'CronJob')
        image = cron['spec']['jobTemplate']['spec']['template']['spec']['containers'][0]['image']

        assert image.startswith('postgres:'), image
        assert image.rsplit(':', 1)[1] != 'latest', image

    def test_the_volume_is_writable_by_the_user_that_writes_to_it(self):
        """The Postgres image runs as uid 70, and a PVC is root-owned
        without `fsGroup`. Same failure as the portal's nginx: it starts,
        it cannot write, and only the logs say so."""
        rendered = _render(*_MINIMAL, '--set', 'backup.enabled=true', '--set', 'backup.existingClaim=c')
        cron = next(m for m in rendered if m['kind'] == 'CronJob')
        spec = cron['spec']['jobTemplate']['spec']['template']['spec']

        assert spec['securityContext']['runAsUser'] == 70
        assert spec['securityContext']['fsGroup'] == 70

    def test_the_script_has_not_drifted_from_the_compose_one(self):
        """Helm cannot read files outside a chart directory, so the copy
        in `charts/igni/files/` is unavoidable. Two backup scripts that
        disagree is worse than one, so they are compared byte for byte."""
        chart_copy = (_CHART / 'files/backup.sh').read_bytes()
        canonical = (_ROOT / 'containers/backup.sh').read_bytes()

        assert chart_copy == canonical, (
            'charts/igni/files/backup.sh has drifted from containers/backup.sh. '
            'Copy the canonical one over it: `cp containers/backup.sh '
            'charts/igni/files/backup.sh`.'
        )

    def test_the_script_is_actually_shipped_in_the_configmap(self):
        """A `.Files.Get` that silently returns nothing renders a
        ConfigMap with an empty key and a CronJob that fails at runtime
        with `No such file`."""
        rendered = _render(*_MINIMAL, '--set', 'backup.enabled=true', '--set', 'backup.existingClaim=c')
        script = next(m for m in rendered if m['metadata']['name'] == 'igni-backup-script')['data']['backup.sh']

        assert 'pg_dump' in script
        assert 'pg_restore --list' in script, 'the verification step is missing from the shipped copy'
        assert 'FROM tenants' in script, 'the shipped copy backs up the shared plane and no organisation'
        assert len(script.splitlines()) > 50, f'only {len(script.splitlines())} lines shipped'


class TestTheLicenceIsMountedWhereEveryComponentReadsIt:
    """The paid modules' licence: a Secret, mounted read-only at the path every component reads.

    See docs/licence-file.md.
    """

    @staticmethod
    def _api(manifests: list[dict]) -> dict:
        return _pod_specs(manifests)[next(name for name in _pod_specs(manifests) if name.endswith('-api'))]

    def test_with_a_secret_the_api_reads_it_at_the_shared_path(self):
        spec = self._api(_render(*_MINIMAL, '--set', 'licence.existingSecret=igni-licence'))

        [mount] = [m for m in spec['containers'][0]['volumeMounts'] if m['name'] == 'licence']
        [volume] = [v for v in spec['volumes'] if v['name'] == 'licence']
        assert mount == {'name': 'licence', 'mountPath': '/etc/igni/licence', 'readOnly': True}
        assert volume['secret'] == {
            'secretName': 'igni-licence',
            'items': [{'key': 'licence.json', 'path': 'licence.json'}],
        }

    def test_without_one_the_base_deploys_with_no_licence_at_all(self):
        spec = self._api(_render(*_MINIMAL))

        assert all(v['name'] != 'licence' for v in spec['volumes'])
