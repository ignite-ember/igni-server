"""The chart's NetworkPolicy is deny-by-default, and the holes are named.

The policy this replaced allowed ingress to the API from every namespace
(`namespaceSelector: {}`), opened every port to each egress CIDR, and
left the portal's ingress alone — while the public docs said it was
"deny-by-default for both directions". It rendered, linted and validated.

So these render the chart with `helm template` and assert on the
policies themselves: that one with no rules selects every pod the release
runs, that ingress comes only from the controller the operator named (and
the portal, for the API), that egress entries keep their ports, and that
the chart refuses to render rather than fall back to "every namespace".

What this does not prove: that a CNI enforces any of it. That is a
property of the cluster, and the chart's own header says so.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_CHART = _ROOT / 'charts/igni'
_HELM = shutil.which('helm')

pytestmark = pytest.mark.skipif(_HELM is None, reason='helm is not installed')

_KEY = 'DUuX49F9ROzfR-UMMTjm65pV86qN7IyUSzC0gkFQDh0='

_MINIMAL = [
    '--set', 'image.repository=registry.example.com/igni/igni-api',
    '--set', 'portalImage.repository=registry.example.com/igni/portal',
    '--set', 'postgres.host=pg.internal',
    '--set', 'redis.host=redis.internal',
    '--set', f'secrets.secretEncryptionKey={_KEY}',
    '--set', 'urls.publicBase=https://igni.example.com',
    # The backup CronJob too, so "every pod" includes it.
    '--set', 'backup.enabled=true',
    '--set', 'backup.existingClaim=igni-backups',
]  # fmt: skip

_CONTROLLER_NS = {'kubernetes.io/metadata.name': 'ingress-nginx'}
_CONTROLLER_PODS = {'app.kubernetes.io/name': 'ingress-nginx'}
_MONITORING_NS = {'kubernetes.io/metadata.name': 'monitoring'}

_VALUES = {
    'networkPolicy': {
        'enabled': True,
        'ingressFrom': {'namespaceSelector': _CONTROLLER_NS, 'podSelector': _CONTROLLER_PODS},
        'egress': [
            {'name': 'postgres', 'cidrs': ['10.0.10.5/32'], 'ports': [{'port': 5432, 'protocol': 'TCP'}]},
            {'name': 'redis', 'cidrs': ['10.0.10.6/32'], 'ports': [{'port': 6379}], 'components': ['api']},
            {
                'name': 'inference',
                'cidrs': ['10.0.40.0/24'],
                'ports': [{'port': 8000}, {'port': 443}],
                'components': ['api'],
            },
        ],
    },
}


def _helm(values: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_HELM or 'helm', 'template', 'igni', str(_CHART), *_MINIMAL, '-f', '-', *args],
        input=yaml.safe_dump(values),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _render(values: dict = _VALUES, *args: str) -> list[dict]:
    result = _helm(values, *args)
    assert result.returncode == 0, f'helm template failed:\n{result.stderr}'
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _refuse(values: dict, *args: str) -> str:
    result = _helm(values, *args)
    assert result.returncode != 0, 'the chart rendered when it should have refused'
    return result.stderr


def _policies(manifests: list[dict]) -> dict[str, dict]:
    return {m['metadata']['name']: m['spec'] for m in manifests if m['kind'] == 'NetworkPolicy'}


def _pod_labels(manifests: list[dict]) -> dict[str, dict]:
    """Every pod template the release runs, keyed by workload name."""
    pods = {}
    for m in manifests:
        if m['kind'] in ('Deployment', 'Job'):
            pods[m['metadata']['name']] = m['spec']['template']['metadata']['labels']
        elif m['kind'] == 'CronJob':
            pods[m['metadata']['name']] = m['spec']['jobTemplate']['spec']['template']['metadata']['labels']
    return pods


def _selects(selector: dict, labels: dict) -> bool:
    """A LabelSelector, evaluated the way the API server does for the
    operators this chart uses."""
    if any(labels.get(k) != v for k, v in selector.get('matchLabels', {}).items()):
        return False
    for expr in selector.get('matchExpressions', []):
        assert expr['operator'] == 'In', expr
        if labels.get(expr['key']) not in expr['values']:
            return False
    return True


def _with(**network_policy) -> dict:
    return {'networkPolicy': {**_VALUES['networkPolicy'], **network_policy}}


@pytest.fixture(scope='module')
def manifests() -> list[dict]:
    return _render()


@pytest.fixture(scope='module')
def policies(manifests) -> dict[str, dict]:
    return _policies(manifests)


def _controller_peer() -> dict:
    return {'namespaceSelector': {'matchLabels': _CONTROLLER_NS}, 'podSelector': {'matchLabels': _CONTROLLER_PODS}}


def _portal_peer() -> dict:
    return {
        'podSelector': {
            'matchLabels': {
                'app.kubernetes.io/name': 'igni',
                'app.kubernetes.io/instance': 'igni',
                'app.kubernetes.io/component': 'portal',
            }
        }
    }


class TestEveryPodStartsWithNothing:
    def test_the_release_runs_the_pods_these_tests_think_it_does(self, manifests):
        """If a workload is added, the list below has to be revisited — and
        so does what it needs from the explicit policies."""
        components = {labels['app.kubernetes.io/component'] for labels in _pod_labels(manifests).values()}

        assert components == {'api', 'portal', 'migrate', 'backup'}

    def test_a_policy_with_no_rules_selects_every_pod(self, manifests, policies):
        deny = policies['igni-default-deny']

        assert sorted(deny['policyTypes']) == ['Egress', 'Ingress']
        assert not deny.get('ingress') and not deny.get('egress'), 'the default-deny policy opens something'
        for workload, labels in _pod_labels(manifests).items():
            assert _selects(deny['podSelector'], labels), f'{workload} is not covered by the default deny'

    def test_with_the_policy_off_nothing_renders(self):
        assert not _policies(_render({'networkPolicy': {'enabled': False}}))


class TestIngressComesOnlyFromWhatYouNamed:
    def test_the_api_admits_the_portal_and_the_controller(self, policies):
        api = policies['igni-api-ingress']

        [rule] = api['ingress']
        assert rule['from'] == [_portal_peer(), _controller_peer()]
        assert rule['ports'] == [{'port': 7777, 'protocol': 'TCP'}]

    def test_the_portal_admits_the_controller_and_nothing_else(self, policies):
        portal = policies['igni-portal-ingress']

        [rule] = portal['ingress']
        assert rule['from'] == [_controller_peer()]
        assert rule['ports'] == [{'port': 3000, 'protocol': 'TCP'}]

    def test_no_peer_anywhere_is_every_namespace(self, policies):
        for name, spec in policies.items():
            for rule in spec.get('ingress', []):
                for peer in rule.get('from', []):
                    if 'namespaceSelector' in peer:
                        assert (peer['namespaceSelector'] or {}).get('matchLabels'), name

    def test_the_ingress_policies_select_the_pods_they_mean(self, manifests, policies):
        pods = _pod_labels(manifests)

        assert _selects(policies['igni-api-ingress']['podSelector'], pods['igni-api'])
        assert not _selects(policies['igni-api-ingress']['podSelector'], pods['igni-portal'])
        assert _selects(policies['igni-portal-ingress']['podSelector'], pods['igni-portal'])
        assert _selects(_portal_peer()['podSelector'], pods['igni-portal'])

    def test_without_a_controller_it_refuses_rather_than_allowing_every_namespace(self):
        message = _refuse(_with(ingressFrom={'namespaceSelector': {}, 'podSelector': {}}))

        assert 'networkPolicy.ingressFrom.namespaceSelector' in message
        assert 'every namespace' in message

    def test_prometheus_is_admitted_when_named(self):
        values = _with(metricsFrom={'namespaceSelector': _MONITORING_NS})
        api = _policies(_render(values))['igni-api-ingress']

        assert {'namespaceSelector': {'matchLabels': _MONITORING_NS}} in api['ingress'][0]['from']

    def test_a_service_monitor_without_metrics_from_refuses(self):
        message = _refuse(
            _VALUES,
            '--set', 'monitoring.serviceMonitor.enabled=true',
            '--set', 'secrets.metricsToken=metrics-token',
        )  # fmt: skip

        assert 'networkPolicy.metricsFrom.namespaceSelector' in message

    def test_an_in_cluster_service_can_be_admitted_to_the_api(self):
        peer = {'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'igni-artifacts'}}}
        api = _policies(_render(_with(apiIngressFrom=[peer])))['igni-api-ingress']

        assert peer in api['ingress'][0]['from']


class TestEgressNamesItsPorts:
    def test_each_entry_carries_its_own_ports(self, policies):
        assert policies['igni-egress-postgres']['egress'] == [
            {'to': [{'ipBlock': {'cidr': '10.0.10.5/32'}}], 'ports': [{'port': 5432, 'protocol': 'TCP'}]}
        ]
        assert policies['igni-egress-redis']['egress'][0]['ports'] == [{'port': 6379, 'protocol': 'TCP'}]
        assert policies['igni-egress-inference']['egress'][0]['ports'] == [
            {'port': 8000, 'protocol': 'TCP'},
            {'port': 443, 'protocol': 'TCP'},
        ]

    def test_the_migration_and_the_backup_reach_postgres(self, manifests, policies):
        """Both run against the database: the migrate hook on every
        upgrade, the backup nightly. An entry with no `components`
        applies to both as well as to the API."""
        pods = _pod_labels(manifests)
        postgres = policies['igni-egress-postgres']['podSelector']

        assert _selects(postgres, pods['igni-migrate'])
        assert _selects(postgres, pods['igni-backup'])
        assert _selects(postgres, pods['igni-api'])
        assert not _selects(postgres, pods['igni-portal'])

    def test_components_narrow_an_entry(self, manifests, policies):
        pods = _pod_labels(manifests)
        redis = policies['igni-egress-redis']['podSelector']

        assert _selects(redis, pods['igni-api'])
        assert not any(_selects(redis, pods[w]) for w in ('igni-migrate', 'igni-backup', 'igni-portal'))

    def test_the_portal_reaches_the_api_and_nothing_else(self, manifests, policies):
        pods = _pod_labels(manifests)
        portal_egress = [
            spec for spec in policies.values() if 'Egress' in spec['policyTypes'] and spec.get('egress')
            and _selects(spec['podSelector'], pods['igni-portal'])
        ]  # fmt: skip
        names = sorted(n for n, s in policies.items() if s in portal_egress)

        assert names == ['igni-dns', 'igni-portal-egress']
        [rule] = policies['igni-portal-egress']['egress']
        assert rule['ports'] == [{'port': 7777, 'protocol': 'TCP'}]
        assert _selects(rule['to'][0]['podSelector'], pods['igni-api'])

    def test_dns_is_open_on_53_only(self, policies):
        [rule] = policies['igni-dns']['egress']

        assert rule['ports'] == [{'port': 53, 'protocol': 'UDP'}, {'port': 53, 'protocol': 'TCP'}]
        assert 'to' not in rule

    def test_dns_can_be_pinned_to_the_cluster_resolver(self):
        peer = {
            'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}},
            'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}},
        }
        [rule] = _policies(_render(_with(dns={'to': [peer]})))['igni-dns']['egress']

        assert rule['to'] == [peer]

    def test_an_entry_without_ports_refuses(self):
        message = _refuse(_with(egress=[{'name': 'postgres', 'cidrs': ['10.0.10.5/32']}]))

        assert 'needs ports' in message

    def test_an_entry_without_a_destination_refuses(self):
        message = _refuse(_with(egress=[{'name': 'postgres', 'ports': [{'port': 5432}]}]))

        assert 'needs cidrs' in message

    def test_the_old_egress_cidrs_still_render_on_every_port(self, manifests):
        values = {'networkPolicy': {**_VALUES['networkPolicy'], 'egress': [], 'egressCidrs': ['10.0.0.0/16']}}
        rendered = _render(values)
        legacy = _policies(rendered)['igni-egress-cidrs']

        assert legacy['egress'] == [{'to': [{'ipBlock': {'cidr': '10.0.0.0/16'}}]}]
        pods = _pod_labels(rendered)
        assert _selects(legacy['podSelector'], pods['igni-api'])
        assert _selects(legacy['podSelector'], pods['igni-migrate'])
        assert _selects(legacy['podSelector'], pods['igni-backup'])

    def test_the_policies_validate_against_the_api_schemas(self, manifests, tmp_path):
        kubeconform = shutil.which('kubeconform')
        if not kubeconform:
            pytest.skip('kubeconform is not installed')
        path = tmp_path / 'policies.yaml'
        path.write_text('\n---\n'.join(yaml.safe_dump(m) for m in manifests if m['kind'] == 'NetworkPolicy'))

        result = subprocess.run(
            [kubeconform, '-strict', '-summary', '-kubernetes-version', '1.29.0', str(path)],
            capture_output=True,
            text=True,
            timeout=180,
        )

        assert result.returncode == 0, result.stdout + result.stderr
