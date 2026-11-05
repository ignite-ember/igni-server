"""Every read behind a subject access request.

GDPR Article 15. `app/services/subject_access.py` ran all of this
itself — thirty hand-written statements and a `rows()` closure over a
session it opened — which is the invariant that put that file in D and
the reason it was the worst-graded module in the codebase.

## Why the SQL is still hand-written

It is not ORM, and deliberately. An export has to answer for **columns**,
not for entities: `app/services/subject_access.py` carries a
classification of every column in the schema as included, withheld or
dead, and a test compares that classification against the live database.
Selecting named columns keeps the two in step — a new column is absent
from the export until somebody classifies it, which is the failure
direction that does not leak. `SELECT *` through a model would quietly
disclose whatever was added last.

So the statements move here unchanged, with the reasoning that justifies
each one. What belongs to the service is the report; what belongs here
is the reading.

## One session per query

They shared one, which looks like a consistent snapshot and is not: the
engine sets no isolation level, so Postgres runs them at READ COMMITTED
and each statement already took its own. Splitting changes nothing about
what any of them can see.

## Two places, and only the caller's organisation

Accounts, memberships and the deployment's own audit trail are in the
shared plane. Groups, usage, notifications and the organisation's audit
trail are in each organisation's
own database — and an export reads only the requesting organisation's.
An administrator answering a request answers for their organisation;
another organisation's rows about the same address are that
organisation's to disclose.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.tenancy import TenantSessions
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)

#: The most rows any one activity section returns. A person with years
#: of history would otherwise produce an export nobody can open; the
#: service says so in the report rather than truncating silently.
ACTIVITY_LIMIT = 1000


class SubjectAccessDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    """The reads that make up one person's export."""

    async def _rows(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        """One statement, as a list of plain dicts.

        Dicts rather than models: every caller writes the result
        straight into the report, and the report's shape is the column
        set of whatever table was asked about. A model per table would
        be forty models that exist only to be dumped again.
        """
        async with self.session_scope() as session:
            result = await session.execute(text(sql), params)
            return [dict(row._mapping) for row in result.all()]

    async def _org_rows(self, org_id: str, sql: str, **params: Any) -> list[dict[str, Any]]:
        """`_rows`, in one organisation's own database."""
        async with TenantSessions().session_scope(org_id) as session:
            result = await session.execute(text(sql), params)
            return [dict(row._mapping) for row in result.all()]

    async def _both_trails(self, org_id: str, sql: str, *, limit: int, **params: Any) -> list[dict[str, Any]]:
        """One audit query against the organisation's trail and the deployment's, newest first."""
        rows = await self._rows(sql, lim=limit, **params) + await self._org_rows(org_id, sql, lim=limit, **params)
        rows.sort(key=lambda row: row['occurred_at'], reverse=True)
        return rows[:limit]

    # ── The account itself ────────────────────────────────────────────

    async def account(self, email: str) -> list[dict[str, Any]]:
        """Matched on the address, case-insensitively.

        Keyed on an address rather than a user id because the subject
        may have no account at all and still appear in sign-in attempts.
        """
        return await self._rows(
            'SELECT user_id, email, full_name, auth_provider, created_at, updated_at, '
            'last_sign_in_at, tokens_valid_after, deactivated_at, is_super_admin '
            'FROM users WHERE lower(email) = lower(:e)',
            e=email,
        )

    # ── Where they stand in the deployment ───────────────────────────

    async def deployment_role(self, user_id: str) -> list[dict[str, Any]]:
        return await self._rows(
            'SELECT role, status, email, full_name, pending_email, assigned_by, '
            'created_at, updated_at FROM user_roles WHERE user_id = :u',
            u=user_id,
        )

    async def organisation_memberships(self, user_id: str) -> list[dict[str, Any]]:
        return await self._rows(
            'SELECT org_id, role, status, invited_email, invited_by, created_at, '
            'updated_at FROM org_memberships WHERE user_id = :u',
            u=user_id,
        )

    async def organisations_owned(self, user_id: str) -> list[dict[str, Any]]:
        return await self._rows(
            'SELECT id, slug, display_name, created_at FROM organizations WHERE owner_user_id = :u',
            u=user_id,
        )

    async def group_memberships(self, org_id: str, user_id: str) -> list[dict[str, Any]]:
        return await self._org_rows(
            org_id,
            'SELECT group_id, added_by_user_id, added_at FROM org_group_members WHERE user_id = :u',
            u=user_id,
        )

    # ── What they did ─────────────────────────────────────────────────

    async def model_usage(self, org_id: str, user_id: str, *, limit: int = ACTIVITY_LIMIT) -> list[dict[str, Any]]:
        return await self._org_rows(
            org_id,
            'SELECT org_id, model, input_tokens, output_tokens, total_tokens, duration, '
            'stream, created_at FROM chat_usage WHERE user_id = :u '
            'ORDER BY created_at DESC LIMIT :lim',
            u=user_id,
            lim=limit,
        )

    async def own_activity(self, org_id: str, user_id: str, *, limit: int = ACTIVITY_LIMIT) -> list[dict[str, Any]]:
        return await self._both_trails(
            org_id,
            'SELECT occurred_at, method, route, target_type, target_id, status_code, '
            'outcome, client_host, forwarded_for, user_agent FROM audit_events '
            'WHERE actor_user_id = :u ORDER BY occurred_at DESC LIMIT :lim',
            limit=limit,
            u=user_id,
        )

    async def authentication_attempts(
        self, org_id: str | None, email: str, *, limit: int = ACTIVITY_LIMIT
    ) -> list[dict[str, Any]]:
        """Audit events keyed on the address, not the account.

        Which is what catches the sign-in attempts made before any
        account existed — exactly the rows recorded with no actor id.
        Those are in the deployment's trail; with an organisation, its
        trail is read as well.
        """
        sql = (
            'SELECT occurred_at, method, route, target_type, target_id, status_code, '
            'outcome, client_host, forwarded_for, user_agent FROM audit_events '
            'WHERE lower(actor_email) = lower(:e) ORDER BY occurred_at DESC LIMIT :lim'
        )
        if org_id is None:
            return await self._rows(sql, e=email, lim=limit)
        return await self._both_trails(org_id, sql, limit=limit, e=email)

    # ── What they made ────────────────────────────────────────────────

    async def things_they_created(self, *, org_id: str, user_id: str, email: str) -> dict[str, Any]:
        """Everything authored, owned, registered or decided by them.

        Grouped because the report groups them, and because each query
        here is one answer to the same question rather than a section a
        reader would look up on its own.
        """
        return {
            'groups': await self._org_rows(
                org_id,
                'SELECT id, org_id, name, created_at FROM org_groups WHERE created_by_user_id = :u',
                u=user_id,
            ),
            'group_entries': await self._org_rows(
                org_id,
                'SELECT id, group_id, entry_name, created_at FROM org_group_entries WHERE created_by_user_id = :u',
                u=user_id,
            ),
            # Which extensions they switched on for which group, and when.
            'group_extensions': await self._org_rows(
                org_id,
                'SELECT group_id, extension_id, enabled_at FROM org_group_extensions WHERE enabled_by_user_id = :u',
                u=user_id,
            ),
            # Apps they registered or own, and the extensions they
            # switched on for one.
            'apps': await self._org_rows(
                org_id,
                'SELECT app_id, owner_user_id, created_by_user_id, created_at FROM org_apps '
                'WHERE owner_user_id = :u OR created_by_user_id = :u',
                u=user_id,
            ),
            'app_extensions': await self._org_rows(
                org_id,
                'SELECT app_id, extension_id, enabled_at FROM org_app_extensions WHERE enabled_by_user_id = :u',
                u=user_id,
            ),
            'vm_profiles': await self._org_rows(
                org_id,
                'SELECT id, name, created_at, updated_at FROM vm_profiles '
                'WHERE created_by_user_id = :u OR updated_by_user_id = :u',
                u=user_id,
            ),
            'vm_profile_requests': await self._org_rows(
                org_id,
                'SELECT subject_kind, subject_id, state, decided_at, reason FROM vm_profile_requests '
                'WHERE decided_by_user_id = :u',
                u=user_id,
            ),
            # Commands they asked for, and those run in their own VM.
            'vm_commands': await self._org_rows(
                org_id,
                'SELECT id, producer_kind, producer_id, command, requested_by_kind, requested_by_id, requested_as, '
                'state, created_at, started_at, finished_at, exit_code, output, error FROM vm_commands '
                "WHERE (requested_by_kind = 'person' AND requested_by_id = :u) "
                "OR (producer_kind = 'person' AND producer_id = :u)",
                u=user_id,
            ),
            'vm_jobs': await self._org_rows(
                org_id,
                'SELECT id, command, requested_as, created_at, cancelled_at FROM vm_jobs '
                "WHERE (requested_by_kind = 'person' AND requested_by_id = :u) OR cancelled_by_id = :u",
                u=user_id,
            ),
            'events': await self._org_rows(
                org_id,
                'SELECT extension_id, kind, event_type, payload, thread, text, title, start_run, action, '
                'attachment_ids, state, failed_reason, not_handled_reason, created_at, delivered_at '
                'FROM producer_events '
                "WHERE producer_kind = 'person' AND producer_id = :u",
                u=user_id,
            ),
            'event_attachments': await self._org_rows(
                org_id,
                'SELECT event_id, filename, content_type, size, sha256, created_at, expires_at '
                'FROM service_attachments WHERE event_id IN '
                "(SELECT id FROM producer_events WHERE producer_kind = 'person' AND producer_id = :u)",
                u=user_id,
            ),
            'vm_credentials': await self._org_rows(
                org_id,
                'SELECT producer_kind, producer_id, created_by_kind, created_by_id, created_at, last_used_at, '
                'revoked_at, revoked_reason, revoked_by_id FROM vm_credentials '
                "WHERE (producer_kind = 'person' AND producer_id = :u) "
                "OR (created_by_kind = 'person' AND created_by_id = :u) OR revoked_by_id = :u",
                u=user_id,
            ),
            # Volumes grown: their own VM's, and the ones they grew.
            'producer_volumes': await self._org_rows(
                org_id,
                'SELECT producer_kind, producer_id, gib, grown_by_kind, grown_by_id, grown_as, grown_at '
                "FROM producer_volumes WHERE (producer_kind = 'person' AND producer_id = :u) "
                "OR (grown_by_kind = 'person' AND grown_by_id = :u)",
                u=user_id,
            ),
            'mcp_approvals': await self._org_rows(
                org_id,
                'SELECT server_name, source, approved_at FROM person_mcp_approvals WHERE user_id = :u',
                u=user_id,
            ),
            # What they were told about, and when they read it. Only
            # the ones addressed to them: a row where they are the
            # actor belongs to whoever was told.
            'notifications': await self._org_rows(
                org_id,
                'SELECT id, source, kind, title, body, actor_user_id, actor_email, '
                'target, created_at, delivered_at, read_at '
                'FROM notifications WHERE user_id = :u',
                u=user_id,
            ),
            # Exports of their data here, or ones they asked for.
            'producer_exports': await self._org_rows(
                org_id,
                'SELECT producer_kind, producer_id, requested_by_user_id, requested_at, state, finished_at '
                "FROM producer_exports WHERE (producer_kind = 'person' AND producer_id = :u) "
                'OR requested_by_user_id = :u',
                u=user_id,
            ),
            # Their tokens revoked, here or everywhere.
            'revocations': await self._rows(
                "SELECT subject_id, reason, created_at FROM revocations WHERE kind = 'person' "
                'AND subject_id = :u AND (org_id = :o OR org_id IS NULL)',
                o=org_id,
                u=user_id,
            ),
            # A legal hold on them here, or one they placed.
            'legal_holds': await self._rows(
                'SELECT user_id, reason, placed_by_user_id, placed_at FROM legal_holds '
                'WHERE org_id = :o AND (user_id = :u OR placed_by_user_id = :u)',
                o=org_id,
                u=user_id,
            ),
            # A legal hold they placed on an app (§18.2).
            'app_legal_holds': await self._rows(
                'SELECT app_id, reason, placed_by_user_id, placed_at FROM app_legal_holds '
                'WHERE org_id = :o AND placed_by_user_id = :u',
                o=org_id,
                u=user_id,
            ),
            # What the gateway was last told about their VM in this organisation.
            'producer_feed': await self._rows(
                'SELECT producer_kind, producer_id, active, removed, removed_at, erase_at, changed_at, '
                'seen_removed_at, erased, legal_hold, erasure_done_at, discarded_by_user_id, discarded_at '
                'FROM producer_feed '
                "WHERE org_id = :o AND producer_kind = 'person' AND producer_id = :u",
                o=org_id,
                u=user_id,
            ),
            'inference_nodes': await self._rows(
                'SELECT node_id, base_url, created_at FROM inference_nodes WHERE created_by_user_id = :u',
                u=user_id,
            ),
        }
