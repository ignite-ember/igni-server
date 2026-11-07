"""What a customer's monitoring can watch, and nothing more.

LG-7. Until now the only signals a self-hosted deployment produced were
``/health`` and ``/ready``: alive, and able to reach its database. Neither
says whether the audit trail is being written or whether retention ran.

**This does not monitor anything.** It exposes numbers in Prometheus text
format on ``/metrics`` for whatever the customer already runs. A
self-hosted product cannot alert its own operator, and shipping an agent
that phones somewhere would contradict the claim the whole product rests
on (DP-5, ``tests/test_nothing_phones_home.py``). The alert rules that go
with these numbers ship as a file — ``charts/igni/files/alerts.yaml`` —
because the rule is the useful part and it is worthless if we keep it.

Three constraints shaped what is here.

**Every metric answers a question somebody would page for.** There is no
request histogram, no per-endpoint latency, no GC stats. Those belong to
a performance investigation, and a metrics endpoint that dumps everything
is one nobody reads. The list started from LG-6 — the audit recorder
fails open and logs ``AUDIT WRITE FAILED``, and "alertable in principle"
was the whole of its evidence — and grew only where a failure was
already known to be silent.

**No label may carry personal data, and none may be unbounded.** A label
value becomes a distinct time series, kept forever, in a system that is
not ours and may be shared across teams. An email address in a label is
a GDPR problem in somebody else's Prometheus; a raw URL path in one is a
cardinality problem that takes the monitoring down. So route labels are
the *template* — ``/v1/portal/groups/{group_id}`` — and a test enforces
the allowlist of label names.

**A gauge that nothing sets reads as zero, which reads as healthy.**
``igni_retention_last_success_timestamp_seconds`` is the dangerous shape:
a deployment where the retention runner never started reports 0, which is
1970, which any sane alert fires on. That is deliberate — the alert
should fire — but it is why the metric is a *timestamp of last success*
rather than a count of runs.
"""

from __future__ import annotations

import time

from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest, Histogram

# Our own registry, not the process-wide default. `prometheus_client`
# registers platform and GC collectors into the default one on import, so
# a private registry means `/metrics` returns what this module declares
# and nothing that happens to be in the same process.
#
# The other collector this was written against was Flower's, which shared
# this image until DEC-13. The reason survives it: any dependency can
# register into the default registry from an import, and then the
# exposition a customer scrapes changes without anything here changing.
REGISTRY = CollectorRegistry()

# ── The audit trail (LG-6) ───────────────────────────────────────────
#
# The recorder catches everything and logs `AUDIT WRITE FAILED` rather
# than failing the request, because refusing would turn a database hiccup
# into an outage across every privileged endpoint at once. The case that
# matters is a successful privileged mutation with no record of it, and
# this counter is the only thing that would ever notice.
AUDIT_WRITE_FAILURES = Counter(
    'igni_audit_write_failures_total',
    'Audit events that could not be written. Each one is a privileged action with no record.',
    registry=REGISTRY,
)

# ── Retention (DP-6, DP-7) ───────────────────────────────────────────
RETENTION_FAILURES = Counter(
    'igni_retention_failures_total',
    'Retention rounds that raised. Data is being kept past its declared window.',
    registry=REGISTRY,
)

RETENTION_LAST_SUCCESS = Gauge(
    'igni_retention_last_success_timestamp_seconds',
    'Unix time of the last completed retention round. 0 means it has never run.',
    registry=REGISTRY,
)

# ── Dependencies ─────────────────────────────────────────────────────
#
# Set by `/ready`, so it reflects the same check an orchestrator uses to
# decide whether to send traffic. Which means it is only as fresh as the
# last probe — stated here because a stale gauge that looks live is worse
# than an absent one.
DEPENDENCY_UP = Gauge(
    'igni_dependency_up',
    '1 when the dependency answered its last readiness check, 0 when it did not.',
    ['dependency'],
    registry=REGISTRY,
)

# ── Clock skew (B8) ──────────────────────────────────────────────────
#
# Three things in this product compare a timestamp made by one process
# against one made by another, and all three fail silently when the
# clocks disagree:
#
# * **revocation.** `users.tokens_valid_after` is written by Postgres and
#   compared against a token's `iat`, stamped by whichever API replica
#   minted it. A replica five seconds ahead mints tokens that survive a
#   revocation — demonstrated, and the whole reason this gauge exists.
#   The margin the watermark carries is one second, so skew above that is
#   already enough to matter;
# * **the audit trail.** `occurred_at` is the app's clock, and the trail
#   is read newest-first with keyset pagination on that column, so skew
#   between replicas mis-orders it and can skip or repeat a row at a page
#   boundary;
# * **retention.** Windows are computed against the app clock and applied
#   to rows stamped by it, so a skewed replica prunes the wrong day.
#
# Signed: positive means this process is *ahead* of the database, which
# is the direction that defeats revocation.
CLOCK_SKEW = Gauge(
    'igni_clock_skew_seconds',
    "This process's clock minus the database's, in seconds. Positive means the app is ahead.",
    registry=REGISTRY,
)

# ── The database connection pool (F116) ──────────────────────────────
#
# `pool_size + max_overflow` is the whole process's ceiling on concurrent
# queries. Exhaustion is the failure mode nobody recognises from the
# outside: unrelated routes all start returning 500 at once, with nothing
# in particular wrong, because they are queueing behind each other for a
# connection. Counting it separately from other query failures is what
# turns that into a one-line diagnosis.
DB_POOL_TIMEOUTS = Counter(
    'igni_db_pool_timeouts_total',
    'Requests that failed because no database connection came free in time.',
    registry=REGISTRY,
)

# Sampled at scrape time rather than set from a code path, so it reports
# the pool as it is when asked and not as it was when something last
# happened to look. Absent — not zero — until the pool exists.
DB_POOL_IN_USE = Gauge(
    'igni_db_pool_connections_in_use',
    "Database connections checked out of this process's pool right now.",
    registry=REGISTRY,
)

DB_POOL_CAPACITY = Gauge(
    'igni_db_pool_capacity',
    'Concurrent queries this process can run: DB_POOL_SIZE + DB_MAX_OVERFLOW.',
    registry=REGISTRY,
)

# ── Server errors ────────────────────────────────────────────────────
#
# By route template. A 500 is always a bug or an outage, and this is the
# signal a customer's on-call would actually be woken by.
SERVER_ERRORS = Counter(
    'igni_server_errors_total',
    'Responses with a 5xx status, by route template.',
    ['route'],
    registry=REGISTRY,
)

# ── Anonymous email abuse (F112) ─────────────────────────────────────
#
# Four endpoints let an unauthenticated caller make this deployment send
# mail to an address they choose. Sustained throttling means somebody is
# trying to use it as a mail cannon aimed at somebody else's mailbox,
# from the customer's own relay identity — which is how a sending domain
# gets blacklisted and an SES account suspended.
EMAIL_CODES_THROTTLED = Counter(
    'igni_email_codes_throttled_total',
    'Verification codes not sent because a limit was reached, by purpose and reason.',
    ['purpose', 'reason'],
    registry=REGISTRY,
)

# ── Traffic (observational) ──────────────────────────────────────────
#
# Not alerted on, and deliberately so — a request rate has no threshold
# that means anything without knowing the deployment. These exist because
# a customer asked to be able to *see* whatever they want, and "is it
# slow, and where" is the first question anybody asks that the alert
# metrics above cannot answer.
#
# `status` is the class, not the code: 2xx/3xx/4xx/5xx. Four values
# instead of forty, and the exact code is in the logs beside the request.
HTTP_REQUESTS = Counter(
    'igni_http_requests_total',
    'Requests served, by method, route template and status class.',
    ['method', 'route', 'status'],
    registry=REGISTRY,
)

# Buckets chosen for this application rather than left at the library
# default, which tops out at 10s. A chat completion legitimately takes
# longer than that, and a histogram whose last bucket holds a third of
# the traffic answers nothing about the tail.
HTTP_DURATION = Histogram(
    'igni_http_request_duration_seconds',
    'Wall-clock time to serve a request, by route template.',
    ['route'],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
    registry=REGISTRY,
)

# The fair queue in front of the model nodes (`docs/service-identity.md`
# §19): how many requests wait, and how long they waited, per
# organisation. Waits run to minutes — an agent run queues behind others.
CHAT_QUEUE_DEPTH = Gauge(
    'igni_chat_queue_depth',
    'Model requests waiting for a free slot on a node, by organisation.',
    ['org'],
    registry=REGISTRY,
)

CHAT_QUEUE_WAIT = Histogram(
    'igni_chat_queue_wait_seconds',
    'How long a model request waited for a slot, by organisation.',
    ['org'],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0),
    registry=REGISTRY,
)

# Pairs with `igni_audit_write_failures_total`: on its own a failure count
# says nothing about whether the trail is being written at all, and "zero
# failures" reads identically on a healthy deployment and on one where the
# middleware stopped firing.
AUDIT_EVENTS_WRITTEN = Counter(
    'igni_audit_events_written_total',
    'Audit events successfully written.',
    registry=REGISTRY,
)

# ── Identity ─────────────────────────────────────────────────────────
#
# Always 1. The value is the label: it lets an alert notice that two
# replicas are serving different versions, which is what a half-finished
# rollout looks like from outside.
BUILD_INFO = Gauge(
    'igni_build_info',
    'Always 1. The version label is the payload.',
    ['version'],
    registry=REGISTRY,
)


def note_retention_success() -> None:
    RETENTION_LAST_SUCCESS.set(time.time())


def set_dependency(name: str, healthy: bool) -> None:
    DEPENDENCY_UP.labels(dependency=name).set(1 if healthy else 0)


# Metrics that intentionally have no alert rule, with the reason. A test
# requires every metric to be either alerted on or named here: the point
# is that "no rule" has to be a decision somebody wrote down, not an
# omission. Without this list the rule was "every metric must have an
# alert", which was too strict the moment a customer wanted numbers they
# could look at rather than be woken by.
OBSERVATIONAL = {
    'igni_http_requests_total': 'A request rate has no threshold that means anything without knowing the deployment.',
    'igni_http_request_duration_seconds': (
        'Latency targets belong to whoever operates the deployment; a chat completion '
        'and a group listing do not share one.'
    ),
    'igni_db_pool_connections_in_use': (
        'A full pool is not a fault — it is a busy process, and how full is too full '
        'depends on the query mix. The alert is on the timeouts it produces.'
    ),
    'igni_db_pool_capacity': 'A configured constant. It is here so the in-use figure has a denominator.',
    'igni_chat_queue_depth': (
        'A queue in front of busy nodes is the design working, not a fault; how long is too long '
        'depends on the deployment. The 429s it gives up with are in the request metrics.'
    ),
    'igni_chat_queue_wait_seconds': (
        'Waits of minutes are expected for agent runs on a saturated deployment; the threshold is '
        "the operator's, against their own nodes."
    ),
    'igni_audit_events_written_total': (
        'The denominator for the failure counter. Alerting on it would mean alerting on a quiet afternoon.'
    ),
}


def is_observational(name: str) -> bool:
    """Whether `name` is a declared observational metric.

    Suffix-insensitive, because a Counter is declared and documented as
    `igni_http_requests_total` and reported by `REGISTRY.collect()` as
    `igni_http_requests`. Comparing the two forms directly is how the
    first version of the exemption list matched nothing.
    """
    return name in OBSERVATIONAL or f'{name}_total' in OBSERVATIONAL


def _sample_pool() -> None:
    """Read the live pool into its gauges, just before rendering.

    Imported here rather than at module scope: `app.db.sql` imports this
    module for the timeout counter, so the dependency only works in one
    direction at import time.
    """
    try:
        # The other half of the cycle named in app/db/sql/__init__.py.
        # Deferred on both sides: whichever one is hoisted, the other
        # has to stay.
        from app.db.sql import SQLAlchemySessionManager

        status = SQLAlchemySessionManager.pool_status()
    except Exception:  # noqa: BLE001 — metrics must never break a scrape
        return
    if status is None:
        return
    in_use, capacity = status
    DB_POOL_IN_USE.set(in_use)
    DB_POOL_CAPACITY.set(capacity)


def render() -> bytes:
    """The Prometheus text exposition of everything above."""
    _sample_pool()
    return generate_latest(REGISTRY)
