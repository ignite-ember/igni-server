"""Every read behind a subject access request (GDPR Article 15).

`SubjectAccessDB` answers, for one person, what the deployment holds:
their account, where they stand, what they did and what they made. Each
read returns its own row model from `app.db.sql.schemas.subject_access`,
except `things_they_created`: `CreatedThingsDB`
(`app/db/sql/subject_access_created.py`) reads that, into the rows of
`app.db.sql.schemas.subject_access_created`.

The SQL selects named columns rather than loading ORM entities. An
export answers for columns: `app/services/subject_access/personal_columns.py`
classifies every column in the schema as included, withheld or dead, and
a test checks every statement here against that classification. A new
column stays out of the export until somebody classifies it — the
failure direction that does not leak.

Accounts, memberships and the deployment's audit trail are in the shared
plane; groups, usage, notifications and the organisation's audit trail
are in each organisation's own database, and only the requesting
organisation's is read. How a statement runs is `SubjectReads`
(`app/db/sql/subject_access_reads.py`).
"""

from __future__ import annotations

from app.db.sql.schemas.subject_access import (
    AccountRow,
    AuditEventRow,
    DeploymentRoleRow,
    GroupMembershipRow,
    ModelUsageRow,
    OrganisationMembershipRow,
    OrganisationOwnedRow,
)
from app.db.sql.schemas.subject_access_created import ThingsTheyCreated
from app.db.sql.subject_access_created import CreatedThingsDB
from app.db.sql.subject_access_reads import SubjectBinds, SubjectReads
from app.db.sql.tenancy import TenantSessions
from app.utils.singleton import SingletonMeta

#: The most rows any one activity section returns. A person with years
#: of history would otherwise produce an export nobody can open; the
#: service says so in the report rather than truncating silently.
ACTIVITY_LIMIT = 1000


class SubjectAccessDB(SubjectReads, metaclass=SingletonMeta):
    """The reads that make up one person's export."""

    def __init__(
        self, *, tenant_sessions: TenantSessions | None = None, created: CreatedThingsDB | None = None
    ) -> None:
        super().__init__(tenant_sessions=tenant_sessions)
        self._created = created or CreatedThingsDB(tenant_sessions=self._tenant_sessions)

    async def _both_trails(self, org_id: str, sql: str, binds: SubjectBinds, *, limit: int) -> list[AuditEventRow]:
        """One audit query against the organisation's trail and the deployment's, newest first."""
        binds = binds.model_copy(update={'limit': limit})
        rows = await self._rows(AuditEventRow, sql, binds) + await self._org_rows(org_id, AuditEventRow, sql, binds)
        rows.sort(key=lambda row: row.occurred_at, reverse=True)
        return rows[:limit]

    # ── The account itself ────────────────────────────────────────────

    async def account(self, email: str) -> list[AccountRow]:
        """Matched on the address, case-insensitively.

        Keyed on an address rather than a user id because the subject
        may have no account at all and still appear in sign-in attempts.
        """
        return await self._rows(
            AccountRow,
            'SELECT user_id, email, full_name, auth_provider, created_at, updated_at, '
            'last_sign_in_at, tokens_valid_after, deactivated_at, is_super_admin '
            'FROM users WHERE lower(email) = lower(:e)',
            SubjectBinds(email=email),
        )

    # ── Where they stand in the deployment ───────────────────────────

    async def deployment_role(self, user_id: str) -> list[DeploymentRoleRow]:
        return await self._rows(
            DeploymentRoleRow,
            'SELECT role, status, email, full_name, pending_email, assigned_by, '
            'created_at, updated_at FROM user_roles WHERE user_id = :u',
            SubjectBinds(user_id=user_id),
        )

    async def organisation_memberships(self, user_id: str) -> list[OrganisationMembershipRow]:
        return await self._rows(
            OrganisationMembershipRow,
            'SELECT org_id, role, status, invited_email, invited_by, created_at, '
            'updated_at FROM org_memberships WHERE user_id = :u',
            SubjectBinds(user_id=user_id),
        )

    async def organisations_owned(self, user_id: str) -> list[OrganisationOwnedRow]:
        return await self._rows(
            OrganisationOwnedRow,
            'SELECT id, slug, display_name, created_at FROM organizations WHERE owner_user_id = :u',
            SubjectBinds(user_id=user_id),
        )

    async def group_memberships(self, org_id: str, *, user_id: str) -> list[GroupMembershipRow]:
        return await self._org_rows(
            org_id,
            GroupMembershipRow,
            'SELECT group_id, added_by_user_id, added_at FROM org_group_members WHERE user_id = :u',
            SubjectBinds(user_id=user_id),
        )

    # ── What they did ─────────────────────────────────────────────────

    async def model_usage(self, org_id: str, *, user_id: str, limit: int = ACTIVITY_LIMIT) -> list[ModelUsageRow]:
        return await self._org_rows(
            org_id,
            ModelUsageRow,
            'SELECT org_id, model, input_tokens, output_tokens, total_tokens, duration, '
            'stream, created_at FROM chat_usage WHERE user_id = :u '
            'ORDER BY created_at DESC LIMIT :lim',
            SubjectBinds(user_id=user_id, limit=limit),
        )

    async def own_activity(self, org_id: str, *, user_id: str, limit: int = ACTIVITY_LIMIT) -> list[AuditEventRow]:
        return await self._both_trails(
            org_id,
            'SELECT occurred_at, method, route, target_type, target_id, status_code, '
            'outcome, client_host, forwarded_for, user_agent FROM audit_events '
            'WHERE actor_user_id = :u ORDER BY occurred_at DESC LIMIT :lim',
            SubjectBinds(user_id=user_id),
            limit=limit,
        )

    async def authentication_attempts(
        self, org_id: str | None, *, email: str, limit: int = ACTIVITY_LIMIT
    ) -> list[AuditEventRow]:
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
            return await self._rows(AuditEventRow, sql, SubjectBinds(email=email, limit=limit))
        return await self._both_trails(org_id, sql, SubjectBinds(email=email), limit=limit)

    # ── What they made ────────────────────────────────────────────────

    async def things_they_created(self, *, org_id: str, user_id: str) -> ThingsTheyCreated:
        """Everything authored, owned, registered or decided by them; see `CreatedThingsDB`."""
        return await self._created.things_they_created(org_id=org_id, user_id=user_id)
