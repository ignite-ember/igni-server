# Seeing what your deployment is doing

Everything here runs inside your infrastructure and sends nothing
anywhere you have not named. There is no telemetry to us, no online licence
check and no phone-home — asserted by `tests/test_nothing_phones_home.py`,
not by this sentence. The one exporter, OpenTelemetry (§5), sends to your
collector, has no default, and is off until you name one.

That has a consequence worth stating plainly: **we cannot see your
deployment, so we cannot tell you it is broken.** What we can do is emit
the numbers, publish the rules we would alert on, and make the logs
machine-readable. The rest is yours, which is the trade you made when you
chose to self-host.

Five things to wire up, in the order they pay off.

## 1. Logs, as JSON

```bash
LOG_FORMAT=json
```

The default is for a person reading `docker logs`:

```
2026-09-02 14:31:02.881 | WARNING:app.security.startup_checks:No SMTP relay…
```

Unambiguous to a human, and one long string to Loki, OpenSearch or
CloudWatch. `LOG_FORMAT=json` emits the same records as objects:

```json
{"ts":"2026-09-02T14:31:02.881Z","level":"WARNING","logger":"app.security.startup_checks","message":"No SMTP relay…"}
```

Fields: `ts` (UTC, ISO 8601, milliseconds), `level`, `logger`, `message`,
plus `exception` when there is a traceback, plus `repo`, `event_id` and
`commit` when a caller sets the request context. Those last three are the
reason to bother — the text format renders them as an opaque
`[acme/api|evt:42|abc1234]` prefix, and as fields they answer "everything
that happened while indexing this repository" with a filter instead of a
regular expression.

**Nothing sets them today.** They were written for the Celery task that
indexed a push, and DEC-13 removed that; the filter is still installed
and the keys are still in the fixed list, so the fields are present and
empty rather than absent. `app/logging_context.py` says the same thing at
more length, including why the keys were kept.

The field list is fixed. Nothing a caller attaches with `extra=` reaches
the output, which is deliberate: that would be an open channel for
whatever somebody logged in a hurry.

**One thing to know before you ship these anywhere.** With
`LOG_SIGN_IN_CODES=true` and no mail relay, sign-in and password-reset
codes are written to the application log. It is off by default and meant
for first setup only; while it is on, the server warns at startup, in
the same line as every code it writes, and to super admins as a banner
in the portal (`GET /v1/portal/deployment/notices`, which carries every
startup warning). Anybody who can read those logs
can sign in as anybody, so either keep it off, or treat the log stream as
credential material and scope access to it accordingly. With it off, the
log records that a code was issued and not delivered, never the code.

## 2. Metrics

```bash
METRICS_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
```

`GET /metrics`, Prometheus text format, on the same port as the API.
**Empty token means the endpoint answers 404** — it is not published
unauthenticated, because the exposition names every route template in the
product and the version each replica is running.

```yaml
scrape_configs:
  - job_name: igni
    authorization:
      credentials: <METRICS_TOKEN>
    static_configs:
      - targets: ['igni-api:7777']
```

On Kubernetes with the Prometheus Operator, the chart renders the
`ServiceMonitor` for you:

```bash
helm upgrade igni ./charts/igni -f my-values.yaml \
  --set monitoring.serviceMonitor.enabled=true \
  --set secrets.metricsToken="$METRICS_TOKEN"
```

What is exposed, and why each one is there:

| Metric | Question it answers |
|---|---|
| `igni_audit_write_failures_total` | Did a privileged action happen with no record of it? The recorder fails open on purpose, so this is the only thing that would notice |
| `igni_audit_events_written_total` | The denominator. Zero failures reads the same on a healthy deployment and on one where the middleware stopped firing |
| `igni_dependency_up` | Can the API reach Postgres and Redis? From the same check `/ready` uses |
| `igni_server_errors_total` | 5xx by route template. Always a bug or an outage; client mistakes are 4xx and are not counted here |
| `igni_http_requests_total` | Traffic by method, route template and status class |
| `igni_http_request_duration_seconds` | Latency by route. Buckets run to 120s, because a chat completion is not a page load |
| `igni_retention_last_success_timestamp_seconds` | When retention last completed. `0` means never |
| `igni_retention_failures_total` | Retention rounds that raised — running, but not pruning |
| `igni_build_info` | Always 1; the `version` label catches a stuck rollout |

**No label carries an identifier.** Routes are templates
(`/v1/portal/groups/{group_id}`), never paths, and there is no user, org
or email label anywhere. That is a deliberate constraint with a test
behind it: a label value is a time series kept forever in a system that
may be shared across your teams, so an identifier there is both a
privacy problem and unbounded cardinality.

## 3. Alerts

`charts/igni/files/alerts.yaml` — seven Prometheus rules, each with a
`for:` window, a severity, and a description that says what to do rather
than restating its own expression. Load it as a rule file, or let the
chart render it:

```bash
helm upgrade igni ./charts/igni -f my-values.yaml \
  --set monitoring.prometheusRule.enabled=true
```

The one to wire into whatever wakes somebody up is
**`IgniAuditWriteFailing`**. It fires on a single occurrence, because one
lost audit record is one privileged action with no evidence it happened.

`charts/igni/files/dashboard.json` is a nine-panel Grafana overview,
generated from the metric definitions by `tools/gen_dashboard.py` and
checked by a test that every expression names a metric the application
actually emits. Import it, or render the ConfigMap for the Grafana
sidecar:

```bash
helm upgrade igni ./charts/igni -f my-values.yaml \
  --set monitoring.dashboard.enabled=true
```

## 4. The audit trail, into your SIEM

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  "https://igni.example.com/v1/portal/audit-events/export?since=2026-09-01T00:00:00Z"
```

JSON Lines, one event per line, **oldest first**, up to 50 000 rows a
call. Store the last `occurred_at` you ingested and pass it as `since` to
resume; the boundary is inclusive, so you may re-read one row rather than
skip one. A duplicate is harmless — every event has a stable id — and a
gap in an audit trail is the thing the table exists not to have.

If you get exactly `limit` lines there are probably more, so keep going
until a call returns fewer.

Admin-only, scoped to the caller's organisation from the token, and the
export **is itself audited**: pulling a copy of the audit trail is a
privileged read, and the record of it answers "who took a copy". The
record holds the row count, never the rows.

For interactive review rather than ingestion, `GET
/v1/portal/audit-events` pages the same events newest-first with filters
for actor, target and outcome. `outcome=denied` is the one worth reading
weekly.

## 5. Traces, to your OpenTelemetry collector

Off by default, and there is **no default endpoint**: with
`OTEL_EXPORTER_OTLP_ENDPOINT` unset no tracer provider is built and nothing
is exported. Name your collector to turn it on:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.observability:4318   # base URL
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf      # or grpc (then the gRPC address)
OTEL_EXPORTER_OTLP_HEADERS=authorization=Bearer%20<token>   # optional, URL-encoded
OTEL_SERVICE_NAME=igni-server                  # the default
```

These standard variables are the only source: not the `.env` file the other
settings read, not the database, not an admin screen. Where the API runs is
the operator's network, so the collector is too; there is no allow-list on the
server side, and an egress policy that keeps the API inside your network keeps
its traces there.

What is traced:

| Span | From | Carries |
|---|---|---|
| A request | FastAPI | Method, route template (`/v1/portal/groups/{group_id}`), status, duration. An incoming `traceparent` is accepted, so a turn the igni client traced continues here |
| An outgoing call | httpx | Method, host, status, duration — to an inference node, a module, an identity provider. It carries `traceparent` on, so the trace continues at the node or module if it traces too |
| A SQL statement | SQLAlchemy | The parameterised statement (`… WHERE id = $1`) and the database; bound values are not recorded |

`/metrics`, `/health`, `/ready` and `/version` are not traced.

**Not exported:** request and response bodies, headers, the query string and
full URL (an OAuth callback's `code` is in one), and the caller's address and
user agent. They are dropped before export, whatever an instrumentation
recorded (`app/observability/otel.py`). Traces only — the metrics are §2's.

The igni client can export to the same collector: `telemetry.otlp` in its
managed settings, or the same `OTEL_*` variables (igni
`docs/CONFIGURATION.md`, "OpenTelemetry"). Its spans carry no prompt, response,
file content or tool argument unless the organisation sets
`capture_content: true`.

## What we do not collect

No usage reporting, no crash reporting, no model telemetry, no analytics
in the portal beyond an optional GTM tag that is gated on the vendor's
own hostname and therefore inert in your deployment. OpenTelemetry (§5)
exports only to a collector you name, and never to us. The igni client keeps
its framework's own run telemetry (Agno's, to `os-api.agno.com`) off, and
forces it off under air-gapped mode. The CLI's update check against PyPI can
be switched off. See `docs/COMPLIANCE_TRACKER.md` row DP-5, and the test
that defends the claim.
