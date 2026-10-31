"""An append-only record of privileged activity.

`COMPLIANCE.md` recorded the absence of this as the largest technical gap
in the product: SOC 2 CC7.2, ISO 27001 Annex A 8.15, and GDPR Article 33
by implication, because a breach cannot be scoped from records that do not
exist. Before this table, an administrator could delete an organisation,
change a member's role or register an inference node and
leave nothing behind but two `logger.info` calls in `inference.py`.

Three decisions in the schema are worth stating, because each is the
opposite of what the rest of this codebase does.

**No foreign keys.** Everywhere else, provenance columns reference the row
they describe — `created_by_user_id` to `users`, `org_id` to
`organizations`. Here they must not. An audit record's whole purpose is to
outlive the thing it describes: "who deleted this organisation" has to
survive the organisation, and F85 showed what a delete rule does to a row
that references something removed. So `actor_user_id`, `org_id` and
`target_id` are plain text.

**Deliberately denormalised.** `actor_email` is a snapshot taken at the
time of the action, not a join. A join would return nothing once the user
is deleted — which is exactly when the record matters most — and it would
also *change* what a historical record says if the user later changes
their address. An audit row must mean the same thing in a year.

**Append-only, enforced by the database.** The migration installs a
trigger that rejects `UPDATE`. Nothing in the application updates these
rows, but "nothing in the application does it" is a weaker statement than
"the database refuses", and the difference is what an auditor is asking
about. `DELETE` is permitted, because retention requires it; that
asymmetry is documented in the migration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

from sqlalchemy import Column, Index, Integer, Text, text, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declared_attr, Mapped

from app.db.sql.models import Base, TenantBase


class AuditColumns:
    """One privileged action, as it happened. The columns of both audit tables.

    Two tables, one shape. An action inside an organisation is recorded in
    that organisation's own database (`AuditEvent`), with the rest of its
    data; an action on the deployment itself — signing in, a super admin's
    change, a deployment setting — has no organisation and is recorded in
    the shared plane (`DeploymentAuditEvent`).
    """

    @declared_attr.directive
    def __table_args__(cls) -> tuple:  # noqa: N805 — SQLAlchemy calls this on the class
        return (
            # The three questions an auditor actually asks, in the order they
            # ask them: what happened recently, what did this person do, and
            # what happened to this thing.
            Index('idx_audit_events_occurred_at', text('occurred_at DESC')),
            Index('idx_audit_events_actor', 'actor_user_id', text('occurred_at DESC')),
            Index('idx_audit_events_target', 'target_type', 'target_id'),
            Index('idx_audit_events_org', 'org_id', text('occurred_at DESC')),
            # And the fourth, which is a person asking what this deployment
            # holds about them. Keyed on the *address* rather than the user
            # id, because the sign-in attempts made before an account existed
            # are recorded with no actor id at all — so `idx_audit_events_actor`
            # cannot reach exactly the rows an Article 15 answer needs.
            #
            # `lower(...)` because that is what `subject_access.py` writes and
            # an index on the bare column would sit unused. Without it this
            # was a sequential scan: 288-344 ms at two million rows, growing
            # for the life of a deployment, since audit retention defaults to
            # keeping everything. F121.
            Index('idx_audit_events_actor_email', text('lower(actor_email)'), text('occurred_at DESC')),
        )

    id: Mapped[str] = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))

    occurred_at: Mapped[datetime] = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text('now()'))

    # ── Who ──────────────────────────────────────────────────────────
    #
    # Nullable because not every recorded action has an authenticated
    # actor: a failed sign-in is one of the most important things in an
    # audit trail and by definition has none.
    actor_user_id: Mapped[str | None] = Column(Text, nullable=True)
    actor_email: Mapped[str | None] = Column(Text, nullable=True)
    org_id: Mapped[str | None] = Column(Text, nullable=True)

    # ── What ─────────────────────────────────────────────────────────
    #
    # The route *template* rather than the request path: recording
    # `/v1/portal/organizations/{org_id}` keeps the cardinality of this
    # column bounded and puts the identifier in `target_id`, where it can
    # be indexed and searched.
    method: Mapped[str] = Column(Text, nullable=False)
    route: Mapped[str] = Column(Text, nullable=False)
    target_type: Mapped[str | None] = Column(Text, nullable=True)
    target_id: Mapped[str | None] = Column(Text, nullable=True)

    # ── Outcome ──────────────────────────────────────────────────────
    #
    # The status code, plus a coarse classification so a reviewer can
    # filter for refusals without knowing HTTP. A run of `denied` against
    # one actor is the pattern worth alerting on, and it is invisible if
    # only successes are recorded.
    status_code: Mapped[int] = Column(Integer, nullable=False)
    outcome: Mapped[str] = Column(Text, nullable=False)

    # ── Where from ───────────────────────────────────────────────────
    #
    # Both, deliberately. `client_host` is the peer that actually
    # connected — behind a reverse proxy that is the proxy. `forwarded_for`
    # is the header it presented, which is what identifies the real client
    # and is also trivially spoofable by that client. Recording one as
    # fact and the other as a claim is the only honest option; collapsing
    # them into a single "ip" column would launder the difference.
    client_host: Mapped[str | None] = Column(Text, nullable=True)
    forwarded_for: Mapped[str | None] = Column(Text, nullable=True)
    user_agent: Mapped[str | None] = Column(Text, nullable=True)

    # ── Anything the handler chose to add ────────────────────────────
    #
    # Never request bodies. F83 is the reason: a webhook payload carries
    # commit messages and an author email per commit, and this table is
    # long-lived by design, so anything written here is retained for as
    # long as the retention window. Handlers add small, deliberate facts
    # (`{"role": "admin"}`), not whatever arrived.
    detail: Mapped[Any | None] = Column(JSONB, nullable=True)


class AuditEvent(AuditColumns, TenantBase):
    """An action inside one organisation, in that organisation's database."""

    __tablename__ = 'audit_events'


class DeploymentAuditEvent(AuditColumns, Base):
    """An action on the deployment itself, which belongs to no organisation."""

    __tablename__ = 'audit_events'
