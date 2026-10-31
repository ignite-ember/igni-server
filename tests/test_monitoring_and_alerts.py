"""Signals a customer's monitoring can act on, and rules that match them.

F101, closing LG-7 and LG-6. Before this a self-hosted deployment
produced `/health` and `/ready`: alive, and able to reach its database.
Nothing said whether the audit trail was being written or whether
retention had run.

The tests here are mostly about one failure mode, because it is the one
that makes monitoring worse than none: **an alert whose metric is never
emitted**. It renders, it loads, it validates, it sits green forever, and
it appears in a compliance conversation as evidence. So the rules file is
parsed and every metric it names is checked against the app's own
registry — and the two that matter most are exercised through the real
code path rather than incremented by hand in a test.

The other theme is labels. A label value is a distinct time series kept
forever in a system that is not ours and may be shared across teams. An
email address in a label is a GDPR problem in somebody else's
Prometheus; a raw URL path is a cardinality problem that takes the
monitoring down. Hence an allowlist, enforced here.
"""

from __future__ import annotations

import pathlib
import re

from prometheus_client import generate_latest
import pytest
import yaml

from app.config import settings
from app.monitoring import metrics as m

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_ALERTS = _ROOT / 'charts/igni/files/alerts.yaml'

# The `client` fixture is based at http://test/v1, and these routes are
# not under /v1 — they are not part of the product's versioned API.
# Without the absolute URL every request here 404s and every assertion
# about authentication passes for the wrong reason.
_ABSOLUTE = 'http://test'

pytestmark = pytest.mark.anyio


def _rules() -> list[dict]:
    document = yaml.safe_load(_ALERTS.read_text())
    return [rule for group in document['groups'] for rule in group['rules']]


def _declared_metric_names() -> set[str]:
    """Every metric name the app's own registry knows about."""
    names = set()
    for metric in m.REGISTRY.collect():
        names.add(metric.name)
        # `_total` is stripped from counter names by the client library's
        # collect(); the exposition and any PromQL referring to it use the
        # suffixed form, so both are accepted.
        names.add(f'{metric.name}_total')
    return names


class TestTheRulesFileIsLoadable:
    """Everything below reads this file. If the parse is wrong, the
    assertions are about nothing."""

    def test_it_parses_as_prometheus_rule_groups(self):
        document = yaml.safe_load(_ALERTS.read_text())

        assert 'groups' in document, document.keys()
        assert len(document['groups']) >= 2, document['groups']

    def test_it_contains_the_rules_it_claims_to(self):
        names = {rule['alert'] for rule in _rules()}

        assert len(names) >= 6, names
        assert 'IgniAuditWriteFailing' in names, (
            'the rule LG-7 exists for is missing — the audit recorder fails open, and '
            'this is the only thing that would notice'
        )


class TestEveryAlertNamesAMetricThatExists:
    """The failure this file is mostly about. An alert on a metric nothing
    emits is green forever and reads as evidence."""

    def test_every_metric_in_the_rules_is_declared_by_the_app(self):
        declared = _declared_metric_names()
        expressions = ' '.join(str(rule['expr']) for rule in _rules())
        referenced = set(re.findall(r'\bigni_[a-z_]+\b', expressions))

        assert referenced, 'no igni_ metrics referenced at all — the regex is wrong'

        missing = sorted(referenced - declared)
        assert not missing, (
            f'these metrics are alerted on and never emitted: {missing}. Such an alert '
            f'cannot fire. Declared: {sorted(declared)}'
        )

    # `test_every_declared_metric_is_alerted_on` lived here and has been
    # replaced by `TestEveryMetricIsEitherAlertedOnOrDeclaredObservational`
    # below. It was the right rule while the only purpose of a metric was
    # paging — it is what produced the retention-failure alert nobody
    # would have written unprompted — and the wrong one the moment a
    # customer wanted numbers to look at rather than be woken by. The
    # replacement keeps the property that matters: nothing is exposed
    # without somebody deciding whether it deserves a rule.

    def test_every_alert_can_be_acted_on(self):
        for rule in _rules():
            name = rule['alert']
            assert 'for' in rule, f'{name} has no `for:` window, so a single scrape can page somebody'
            assert rule['labels'].get('severity') in ('critical', 'warning', 'info'), rule['labels']

            description = rule['annotations'].get('description', '')
            assert len(description) > 120, (
                f'{name} has a {len(description)}-character description. An alert whose '
                'text is its own expression wakes somebody up with a puzzle.'
            )
            assert rule['annotations'].get('summary'), f'{name} has no summary'

    def test_the_two_retention_rules_do_not_overlap(self):
        """A deployment that never ran retention reports 0, and
        `time() - 0` is fifty-odd years. Without the `> 0` guard the
        "not running" alert fires on every fresh install with a
        nonsensical duration in it."""
        rules = {rule['alert']: str(rule['expr']) for rule in _rules()}

        assert 'igni_retention_last_success_timestamp_seconds > 0' in rules['IgniRetentionNotRunning']
        assert '== 0' in rules['IgniRetentionNeverRan']


class TestNoLabelCarriesPersonalDataOrUnboundedValues:
    # Each of these is a closed set, and that is the whole criterion:
    #
    #   dependency  postgres, redis
    #   purpose     signup, signin, reset — the three code kinds
    #   reason      why a code was throttled, a fixed set
    #   route       route templates, never a path with an identifier in it
    #   version     one per deployed build
    #   method      HTTP verbs
    #   status      the class, 2xx/3xx/4xx/5xx, not the code
    #   le          histogram bucket bounds, from the fixed bucket list
    #   org         an organisation's id: one per organisation of the
    #               deployment — a handful, never a person — for the
    #               model queue's depth and wait (§19.1)
    #
    # `le` and `status` arrived with the traffic metrics and this test
    # found them — but only in a run where requests had actually been
    # served, since a label appears in `collect()` once something is
    # observed. Hence the second loop, which reads the declared names
    # whether or not anything has happened yet.
    _ALLOWED_LABELS = {
        'dependency',
        'le',
        'method',
        'org',
        'purpose',
        'reason',
        'route',
        'status',
        'version',
    }

    def test_the_label_names_are_on_the_allowlist(self):
        used: set[str] = set()
        for metric in m.REGISTRY.collect():
            for sample in metric.samples:
                used |= set(sample.labels)

        # Nothing has been observed yet in a fresh registry, so the
        # declared label names are read from the metric objects too.
        for collector in (
            m.DEPENDENCY_UP,
            m.SERVER_ERRORS,
            m.BUILD_INFO,
            m.HTTP_REQUESTS,
            m.HTTP_DURATION,
            m.EMAIL_CODES_THROTTLED,
            m.CHAT_QUEUE_DEPTH,
            m.CHAT_QUEUE_WAIT,
        ):
            used |= set(collector._labelnames)

        forbidden = sorted(used - self._ALLOWED_LABELS)
        assert not forbidden, (
            f'{forbidden} are used as metric labels. A label value is a time series '
            'kept forever in a system that is not ours: an identifier here is either '
            "somebody's personal data in a third party's Prometheus, or unbounded "
            'cardinality that takes the monitoring down.'
        )

    def test_the_route_label_is_a_template_not_a_path(self):
        """`/v1/portal/groups/{group_id}`, never
        `/v1/portal/groups/8f14e45f`. The second is one series per group
        and, for user-scoped routes, personal data."""
        source = (_ROOT / 'app/middlewares/metrics.py').read_text()

        assert 'full_route_template' in source, (
            'the 5xx counter does not use the audit middleware’s route reconstruction, '
            'so it is probably labelling by raw path'
        )
        assert 'unmatched' in source, 'unmatched requests are not collapsed, so a scanner defines our series'

class TestTheMetricsEndpointIsNotOpen:
    """The exposition lists every route template in the product and the
    version each replica runs. That is a map for somebody deciding what to
    attack, and on a busy deployment a rough business metric."""

    async def test_it_does_not_exist_without_a_token(self, client, monkeypatch):
        """404 rather than 401, so an unconfigured deployment does not
        advertise that the endpoint is here. Same shape as an unset node
        registration token."""
        monkeypatch.setattr(settings, 'metrics_token', '')

        response = await client.get(_ABSOLUTE + '/metrics')

        assert response.status_code == 404, response.status_code

    async def test_a_wrong_token_is_refused(self, client, monkeypatch):
        monkeypatch.setattr(settings, 'metrics_token', 'the-real-token')

        response = await client.get(_ABSOLUTE + '/metrics', headers={'Authorization': 'Bearer wrong'})

        assert response.status_code == 401, response.status_code

    async def test_no_token_at_all_is_refused_when_one_is_configured(self, client, monkeypatch):
        monkeypatch.setattr(settings, 'metrics_token', 'the-real-token')

        response = await client.get(_ABSOLUTE + '/metrics')

        assert response.status_code == 401, response.status_code

    async def test_the_right_token_gets_the_exposition(self, client, monkeypatch):
        monkeypatch.setattr(settings, 'metrics_token', 'the-real-token')

        response = await client.get(_ABSOLUTE + '/metrics', headers={'Authorization': 'Bearer the-real-token'})

        assert response.status_code == 200, response.status_code
        assert 'text/plain' in response.headers['content-type']
        assert 'igni_audit_write_failures_total' in response.text

    async def test_it_carries_no_metrics_from_the_rest_of_the_process(self, client, monkeypatch):
        """A private registry, not the process-wide default. The client
        library registers platform and GC collectors into that one on
        import, and any dependency can add to it from an import too —
        which is how the exposition a customer scrapes changes without
        anything here changing. The collector this was written against
        was Flower's, until DEC-13.
        """
        monkeypatch.setattr(settings, 'metrics_token', 'tok')

        body = (await client.get(_ABSOLUTE + '/metrics', headers={'Authorization': 'Bearer tok'})).text

        assert 'python_gc_objects' not in body, body[:400]
        assert 'process_virtual_memory_bytes' not in body, body[:400]
        for line in body.splitlines():
            if line and not line.startswith('#'):
                assert line.startswith('igni_'), f'unexpected metric in the exposition: {line}'


class TestTheCountersAreWiredToRealFailures:
    """Incremented by exercising the code path, not by calling `.inc()` in
    a test. A counter that only a test increments is the same lie as an
    alert on a metric nothing emits."""

    async def test_a_failing_audit_write_is_counted(self):
        """LG-6, closed. The recorder catches everything and logs; this
        asserts the counter moves on the same path.

        Patching `session_scope` — which is what `record()` actually
        calls — rather than the session manager class. The first version
        patched `SQLAlchemySessionManager` and the counter did not move:
        `AuditEventDB` inherits the scope and never names the class, so
        the patch applied to nothing and the write *succeeded*. A test
        that mocks the wrong thing here would have reported this counter
        working while it was never incremented at all.
        """
        from unittest.mock import patch

        from app.db.sql.audit_events import AuditEventDB, AuditRecord

        before = m.AUDIT_WRITE_FAILURES._value.get()

        record = AuditRecord(
            method='POST',
            route='/v1/portal/groups',
            status_code=200,
            actor_user_id='u1',
            actor_email='someone@example.invalid',
            org_id='o1',
            target_type='group',
            target_id='g1',
            client_host='10.0.0.1',
            user_agent='pytest',
            detail={},
        )

        # Patched on the *instance*, not the class. `AuditEventDB` is a
        # singleton, and something earlier in the suite sets
        # `session_scope` on the shared instance — an instance attribute
        # shadows a class-level patch, so patching the class passed in
        # isolation and failed in the full suite with the write quietly
        # succeeding. An instance patch always wins.
        recorder = AuditEventDB()
        with patch.object(recorder, 'session_scope', side_effect=RuntimeError('the database is gone')):
            await recorder.record(record)

        assert (
            m.AUDIT_WRITE_FAILURES._value.get() == before + 1
        ), 'an audit write failed and the counter did not move, so IgniAuditWriteFailing can never fire'

    def test_retention_stamps_its_last_success(self):
        m.RETENTION_LAST_SUCCESS.set(0)
        m.note_retention_success()

        assert m.RETENTION_LAST_SUCCESS._value.get() > 1_700_000_000

    def test_the_retention_stamp_is_set_from_the_loop(self):
        """Asserted on the source: the call has to be in the loop, after a
        completed round. Inside `run_once` it would be stamped by a manual
        invocation too, and the alert is about the runner."""
        source = (_ROOT / 'app/services/retention.py').read_text()

        assert 'note_retention_success()' in source
        loop = source[source.index('async def _loop') :]
        assert 'note_retention_success()' in loop, 'the stamp is not set from the daily loop'
        assert 'RETENTION_FAILURES.inc()' in loop

    async def test_readiness_populates_the_dependency_gauge(self, client):
        """The gauge mirrors `/ready` rather than running its own check:
        two health checks that can disagree is a support conversation
        nobody wins."""
        await client.get(_ABSOLUTE + '/ready')

        samples = {
            sample.labels['dependency']: sample.value
            for metric in m.REGISTRY.collect()
            if metric.name == 'igni_dependency_up'
            for sample in metric.samples
        }

        assert set(samples) == {'postgres', 'redis'}, samples

    def test_the_build_version_is_exposed(self):
        body = generate_latest(m.REGISTRY).decode()

        assert 'igni_build_info' in body
        assert settings.app_version in body


class TestTheChartShipsTheRulesWithoutOwningThem:
    def test_monitoring_is_off_by_default(self):
        values = yaml.safe_load((_ROOT / 'charts/igni/values.yaml').read_text())

        assert values['monitoring']['serviceMonitor']['enabled'] is False
        assert (
            values['monitoring']['prometheusRule']['enabled'] is False
        ), 'a PrometheusRule in a cluster with no Prometheus Operator fails the install with `no matches for kind`'

    def test_the_scrape_token_is_read_from_the_secret(self):
        """A ServiceMonitor is a plain object readable by anything with
        `get servicemonitors`."""
        template = (_ROOT / 'charts/igni/templates/monitoring.yaml').read_text()

        assert 'bearerTokenSecret' in template
        assert 'key: METRICS_TOKEN' in template

    def test_enabling_the_scrape_without_a_token_refuses(self):
        """Otherwise the ServiceMonitor is installed and scrapes a 404
        forever, which looks like a monitored deployment."""
        template = (_ROOT / 'charts/igni/templates/monitoring.yaml').read_text()

        assert 'fail' in template
        assert 'secrets.metricsToken' in template


class TestTheDashboardOnlyShowsMetricsThatExist:
    """F102. The same failure as an alert on a missing metric, and worse
    because it looks like data: a Grafana panel querying a metric nothing
    emits draws an empty graph, which reads as "quiet" rather than
    "broken"."""

    @pytest.fixture
    def dashboard(self) -> dict:
        import json

        return json.loads((_ROOT / 'charts/igni/files/dashboard.json').read_text())

    def test_it_is_valid_json_with_panels(self, dashboard):
        assert dashboard['uid'] == 'igni-overview'
        assert len(dashboard['panels']) >= 8, len(dashboard['panels'])

    def test_every_expression_names_a_metric_the_app_emits(self, dashboard):
        declared = _declared_metric_names()
        # Histogram buckets are queried as `_bucket`, which the registry
        # reports under the base name.
        declared |= {f'{name}_bucket' for name in declared}

        referenced = set()
        for panel in dashboard['panels']:
            for target in panel['targets']:
                referenced |= set(re.findall(r'\bigni_[a-z_]+\b', target['expr']))

        assert referenced, 'no metrics referenced at all — the parse is wrong'

        missing = sorted(referenced - declared)
        assert not missing, (
            f'the dashboard queries metrics that are never emitted: {missing}. The panel '
            'draws an empty graph, which reads as quiet rather than broken.'
        )

    def test_every_panel_says_what_it_means(self, dashboard):
        """A panel titled "Audit writes" showing a flat line tells a
        customer nothing about whether flat is good."""
        for panel in dashboard['panels']:
            assert len(panel.get('description', '')) > 60, f'panel {panel["title"]!r} has no useful description'

    def test_the_datasource_is_a_variable(self, dashboard):
        """Hardcoding a datasource uid means the dashboard imports and
        every panel says "datasource not found"."""
        names = {v['name'] for v in dashboard['templating']['list']}

        assert 'datasource' in names, dashboard['templating']
        for panel in dashboard['panels']:
            assert panel['datasource']['uid'] == '${datasource}', panel['title']

    def test_it_is_generated_rather_than_hand_edited(self):
        """`tools/gen_dashboard.py` produces it. A hand-edited 8 KB of
        JSON is where the metric names drift out of sync with the code
        that emits them."""
        assert (_ROOT / 'tools/gen_dashboard.py').exists()

        import json
        import subprocess

        before = (_ROOT / 'charts/igni/files/dashboard.json').read_text()
        result = subprocess.run(
            ['.venv/bin/python', 'tools/gen_dashboard.py'], cwd=_ROOT, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        after = (_ROOT / 'charts/igni/files/dashboard.json').read_text()

        assert json.loads(before) == json.loads(after), (
            'the committed dashboard differs from what the generator produces — somebody '
            'edited the JSON. Change tools/gen_dashboard.py instead.'
        )


class TestEveryMetricIsEitherAlertedOnOrDeclaredObservational:
    """The rule replacing "every metric must have an alert", which was
    right while the only purpose was paging and wrong the moment a
    customer wanted numbers to look at. "No rule" now has to be a
    decision somebody wrote down."""

    def test_nothing_is_exposed_by_accident(self):
        expressions = ' '.join(str(rule['expr']) for rule in _rules())

        undeclared = sorted(
            metric.name
            for metric in m.REGISTRY.collect()
            if metric.name not in expressions
            and f'{metric.name}_total' not in expressions
            and not m.is_observational(metric.name)
        )

        assert not undeclared, (
            f'{undeclared} have no alert rule and are not declared observational. Write '
            'the rule, or add it to OBSERVATIONAL with the reason there is none.'
        )

    def test_each_observational_metric_has_a_reason(self):
        for name, reason in m.OBSERVATIONAL.items():
            assert len(reason) > 40, f'{name}: {reason!r} is not a reason'

    def test_the_observational_list_has_no_ghosts(self):
        """A name here that nothing emits is a stale exemption, and a
        stale exemption is how a real metric later slips through with the
        same name."""
        declared = set()
        for metric in m.REGISTRY.collect():
            declared.add(metric.name)
            declared.add(f'{metric.name}_total')

        ghosts = sorted(set(m.OBSERVATIONAL) - declared)
        assert not ghosts, f'{ghosts} are declared observational and are not emitted at all'
