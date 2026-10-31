"""Writes charts/igni/files/dashboard.json."""

import json
import pathlib


def panel(i, title, targets, unit=None, description='', x=0, y=0, w=12, h=8, kind='timeseries'):
    return {
        'id': i,
        'title': title,
        'type': kind,
        'description': description,
        'datasource': {'type': 'prometheus', 'uid': '${datasource}'},
        'gridPos': {'x': x, 'y': y, 'w': w, 'h': h},
        'targets': [
            {
                'expr': expr,
                'legendFormat': legend,
                'refId': chr(65 + n),
                'datasource': {'type': 'prometheus', 'uid': '${datasource}'},
            }
            for n, (expr, legend) in enumerate(targets)
        ],
        'fieldConfig': {'defaults': ({'unit': unit} if unit else {}), 'overrides': []},
    }


panels = [
    panel(
        1,
        'Audit writes',
        [
            ('sum(rate(igni_audit_events_written_total[5m])) * 60', 'written/min'),
            ('sum(rate(igni_audit_write_failures_total[5m])) * 60', 'FAILED/min'),
        ],
        description=(
            'The failure series should be flat at zero. Anything above it is a privileged '
            'action that happened with no record of it — the recorder fails open on purpose, '
            'because refusing would take every privileged endpoint down with the database. '
            'The written series is here as the denominator: zero failures reads the same on a '
            'healthy deployment and on one where the middleware stopped firing.'
        ),
        x=0,
        y=0,
    ),
    panel(
        2,
        'Requests by status class',
        [
            ('sum(rate(igni_http_requests_total[5m])) by (status) * 60', '{{status}}/min'),
        ],
        description=(
            '4xx is clients getting it wrong and is normal traffic — a bad token, a stale '
            'page. 5xx is us, and every one of them is either a bug or an outage. Watch the '
            'ratio rather than the absolute number: a rise in 4xx with flat 5xx usually means '
            'a client or an integration changed, not that anything here broke.'
        ),
        x=12,
        y=0,
    ),
    panel(
        3,
        'Server errors by route',
        [
            ('sum(rate(igni_server_errors_total[5m])) by (route) * 60', '{{route}}'),
        ],
        description=(
            'Route templates, never raw paths — the identifiers are in the logs, filterable '
            'by `logger` and `repo` if LOG_FORMAT=json.'
        ),
        x=0,
        y=8,
    ),
    panel(
        4,
        'Latency by route (p95)',
        [
            (
                'histogram_quantile(0.95, sum(rate(igni_http_request_duration_seconds_bucket[5m])) by (le, route))',
                '{{route}}',
            ),
        ],
        unit='s',
        description=(
            'Buckets run to 120s because a chat completion legitimately takes longer than the '
            "library default's 10s ceiling."
        ),
        x=12,
        y=8,
    ),
    panel(
        5,
        'Dependencies',
        [('igni_dependency_up', '{{dependency}}')],
        description=(
            'From the same check /ready uses, so it is only as fresh as the last probe. '
            '0 means the API is already answering 503 there.'
        ),
        x=0,
        y=16,
        w=8,
        h=6,
        kind='stat',
    ),
    panel(
        6,
        'Retention: hours since last success',
        [
            ('(time() - igni_retention_last_success_timestamp_seconds) / 3600', 'hours'),
        ],
        unit='h',
        description=(
            'Prunes daily. A number in the hundreds of thousands means it has never run, which '
            'is expected when both retention windows are 0 — keep everything forever, a lawful '
            'choice only if you can defend it.'
        ),
        x=8,
        y=16,
        w=8,
        h=6,
        kind='stat',
    ),
    panel(
        7,
        'Versions serving',
        [('count by (version) (igni_build_info)', '{{version}}')],
        description='More than one for more than a few minutes is a stuck rollout.',
        x=16,
        y=16,
        w=8,
        h=6,
        kind='stat',
    ),
    panel(
        8,
        'Retention failures',
        [
            ('sum(increase(igni_retention_failures_total[1h]))', 'per hour'),
        ],
        description=(
            'Distinct from the panel above: this is a runner that is running and raising, '
            'rather than one that stopped. The traceback is in the API log.'
        ),
        x=0,
        y=22,
    ),
]

dashboard = {
    '__inputs': [],
    'title': 'igni',
    'uid': 'igni-overview',
    'tags': ['igni'],
    'timezone': 'utc',
    'schemaVersion': 39,
    'version': 1,
    'refresh': '1m',
    'time': {'from': 'now-6h', 'to': 'now'},
    'editable': True,
    'templating': {
        'list': [
            {
                'name': 'datasource',
                'label': 'Prometheus',
                'type': 'datasource',
                'query': 'prometheus',
                'current': {},
                'hide': 0,
            }
        ]
    },
    'panels': panels,
    'description': (
        'Overview for a self-hosted igni deployment. Eight panels, one per thing that goes '
        'wrong quietly. Generated from the metric definitions in app/monitoring/metrics.py — '
        'see tools/gen_dashboard.py; a test asserts every expression here names a metric '
        'the application actually emits.'
    ),
}

out = pathlib.Path('charts/igni/files/dashboard.json')
out.write_text(json.dumps(dashboard, indent=2) + '\n')
print(f'{out}: {len(panels)} panels, {len(out.read_text())} bytes')
