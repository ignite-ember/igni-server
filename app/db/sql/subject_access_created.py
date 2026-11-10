"""What one person authored, owned, registered or decided: `CreatedThingsDB`.

Part of a subject access request (GDPR Article 15); `SubjectAccessDB`
(`app/db/sql/subject_access.py`) hands this read on to it. Each
statement returns its own row model from
`app.db.sql.schemas.subject_access_created`, and selects named columns
for the reason given there: a test checks every statement here against
`app/services/subject_access/personal_columns.py`.
"""

from __future__ import annotations

from app.db.sql.schemas.subject_access_created import (
    ApiKeyRow,
    AppExtensionRow,
    AppLegalHoldRow,
    CreatedAppRow,
    CreatedGroupRow,
    EventAttachmentRow,
    GroupEntryRow,
    GroupExtensionRow,
    InferenceNodeRow,
    LegalHoldRow,
    McpApprovalRow,
    ProducerEventRow,
    ProducerExportRow,
    ProducerFeedRow,
    ProducerVolumeRow,
    ReceivedNotificationRow,
    RevocationRow,
    ThingsTheyCreated,
    VmCommandRow,
    VmCredentialRow,
    VmJobRow,
    VmProfileRequestRow,
    VmProfileRow,
)
from app.db.sql.subject_access_reads import SubjectBinds, SubjectReads


class CreatedThingsDB(SubjectReads):
    """The statements behind `ThingsTheyCreated`, in the organisation's database and the shared plane."""

    async def things_they_created(self, *, org_id: str, user_id: str) -> ThingsTheyCreated:
        """Everything authored, owned, registered or decided by them.

        Grouped because the report groups them, and because each query
        here is one answer to the same question rather than a section a
        reader would look up on its own.
        """
        return ThingsTheyCreated(
            groups=await self._org_rows(
                org_id,
                CreatedGroupRow,
                'SELECT id, org_id, name, created_at FROM org_groups WHERE created_by_user_id = :u',
                SubjectBinds(user_id=user_id),
            ),
            group_entries=await self._org_rows(
                org_id,
                GroupEntryRow,
                'SELECT id, group_id, entry_name, created_at FROM org_group_entries WHERE created_by_user_id = :u',
                SubjectBinds(user_id=user_id),
            ),
            # Which extensions they switched on for which group, and when.
            group_extensions=await self._org_rows(
                org_id,
                GroupExtensionRow,
                'SELECT group_id, extension_id, enabled_at FROM org_group_extensions WHERE enabled_by_user_id = :u',
                SubjectBinds(user_id=user_id),
            ),
            # Apps they registered or own, and the extensions they
            # switched on for one.
            apps=await self._org_rows(
                org_id,
                CreatedAppRow,
                'SELECT app_id, owner_user_id, created_by_user_id, created_at FROM org_apps '
                'WHERE owner_user_id = :u OR created_by_user_id = :u',
                SubjectBinds(user_id=user_id),
            ),
            app_extensions=await self._org_rows(
                org_id,
                AppExtensionRow,
                'SELECT app_id, extension_id, enabled_at FROM org_app_extensions WHERE enabled_by_user_id = :u',
                SubjectBinds(user_id=user_id),
            ),
            vm_profiles=await self._org_rows(
                org_id,
                VmProfileRow,
                'SELECT id, name, created_at, updated_at FROM vm_profiles '
                'WHERE created_by_user_id = :u OR updated_by_user_id = :u',
                SubjectBinds(user_id=user_id),
            ),
            vm_profile_requests=await self._org_rows(
                org_id,
                VmProfileRequestRow,
                'SELECT subject_kind, subject_id, state, decided_at, reason FROM vm_profile_requests '
                'WHERE decided_by_user_id = :u',
                SubjectBinds(user_id=user_id),
            ),
            # Commands they asked for, and those run in their own VM.
            vm_commands=await self._org_rows(
                org_id,
                VmCommandRow,
                'SELECT id, producer_kind, producer_id, command, requested_by_kind, requested_by_id, requested_as, '
                'state, created_at, started_at, finished_at, exit_code, output, error FROM vm_commands '
                "WHERE (requested_by_kind = 'person' AND requested_by_id = :u) "
                "OR (producer_kind = 'person' AND producer_id = :u)",
                SubjectBinds(user_id=user_id),
            ),
            vm_jobs=await self._org_rows(
                org_id,
                VmJobRow,
                'SELECT id, command, requested_as, created_at, cancelled_at FROM vm_jobs '
                "WHERE (requested_by_kind = 'person' AND requested_by_id = :u) OR cancelled_by_id = :u",
                SubjectBinds(user_id=user_id),
            ),
            events=await self._org_rows(
                org_id,
                ProducerEventRow,
                'SELECT extension_id, kind, event_type, payload, thread, text, title, start_run, action, '
                'attachment_ids, state, failed_reason, not_handled_reason, created_at, delivered_at '
                'FROM producer_events '
                "WHERE producer_kind = 'person' AND producer_id = :u",
                SubjectBinds(user_id=user_id),
            ),
            event_attachments=await self._org_rows(
                org_id,
                EventAttachmentRow,
                'SELECT event_id, filename, content_type, size, sha256, created_at, expires_at '
                'FROM service_attachments WHERE event_id IN '
                "(SELECT id FROM producer_events WHERE producer_kind = 'person' AND producer_id = :u)",
                SubjectBinds(user_id=user_id),
            ),
            vm_credentials=await self._org_rows(
                org_id,
                VmCredentialRow,
                'SELECT producer_kind, producer_id, created_by_kind, created_by_id, created_at, last_used_at, '
                'revoked_at, revoked_reason, revoked_by_id FROM vm_credentials '
                "WHERE (producer_kind = 'person' AND producer_id = :u) "
                "OR (created_by_kind = 'person' AND created_by_id = :u) OR revoked_by_id = :u",
                SubjectBinds(user_id=user_id),
            ),
            api_keys=await self._org_rows(
                org_id,
                ApiKeyRow,
                'SELECT user_id, name, created_at, expires_at, last_used_at, revoked_at, revoked_reason, '
                'revoked_by_id FROM api_keys WHERE user_id = :u OR revoked_by_id = :u',
                SubjectBinds(user_id=user_id),
            ),
            # Volumes grown: their own VM's, and the ones they grew.
            producer_volumes=await self._org_rows(
                org_id,
                ProducerVolumeRow,
                'SELECT producer_kind, producer_id, gib, grown_by_kind, grown_by_id, grown_as, grown_at '
                "FROM producer_volumes WHERE (producer_kind = 'person' AND producer_id = :u) "
                "OR (grown_by_kind = 'person' AND grown_by_id = :u)",
                SubjectBinds(user_id=user_id),
            ),
            mcp_approvals=await self._org_rows(
                org_id,
                McpApprovalRow,
                'SELECT server_name, source, approved_at FROM person_mcp_approvals WHERE user_id = :u',
                SubjectBinds(user_id=user_id),
            ),
            # What they were told about, and when they read it. Only
            # the ones addressed to them: a row where they are the
            # actor belongs to whoever was told.
            notifications=await self._org_rows(
                org_id,
                ReceivedNotificationRow,
                'SELECT id, source, kind, title, body, actor_user_id, actor_email, '
                'target, created_at, delivered_at, read_at '
                'FROM notifications WHERE user_id = :u',
                SubjectBinds(user_id=user_id),
            ),
            # Exports of their data here, or ones they asked for.
            producer_exports=await self._org_rows(
                org_id,
                ProducerExportRow,
                'SELECT producer_kind, producer_id, requested_by_user_id, requested_at, state, finished_at '
                "FROM producer_exports WHERE (producer_kind = 'person' AND producer_id = :u) "
                'OR requested_by_user_id = :u',
                SubjectBinds(user_id=user_id),
            ),
            # Their tokens revoked, here or everywhere.
            revocations=await self._rows(
                RevocationRow,
                "SELECT subject_id, reason, created_at FROM revocations WHERE kind = 'person' "
                'AND subject_id = :u AND (org_id = :o OR org_id IS NULL)',
                SubjectBinds(org_id=org_id, user_id=user_id),
            ),
            # A legal hold on them here, or one they placed.
            legal_holds=await self._rows(
                LegalHoldRow,
                'SELECT user_id, reason, placed_by_user_id, placed_at FROM legal_holds '
                'WHERE org_id = :o AND (user_id = :u OR placed_by_user_id = :u)',
                SubjectBinds(org_id=org_id, user_id=user_id),
            ),
            # A legal hold they placed on an app (§18.2).
            app_legal_holds=await self._rows(
                AppLegalHoldRow,
                'SELECT app_id, reason, placed_by_user_id, placed_at FROM app_legal_holds '
                'WHERE org_id = :o AND placed_by_user_id = :u',
                SubjectBinds(org_id=org_id, user_id=user_id),
            ),
            # What the gateway was last told about their VM in this organisation.
            producer_feed=await self._rows(
                ProducerFeedRow,
                'SELECT producer_kind, producer_id, active, removed, removed_at, erase_at, changed_at, '
                'seen_removed_at, erased, legal_hold, erasure_done_at, discarded_by_user_id, discarded_at '
                'FROM producer_feed '
                "WHERE org_id = :o AND producer_kind = 'person' AND producer_id = :u",
                SubjectBinds(org_id=org_id, user_id=user_id),
            ),
            inference_nodes=await self._rows(
                InferenceNodeRow,
                'SELECT node_id, base_url, created_at FROM inference_nodes WHERE created_by_user_id = :u',
                SubjectBinds(user_id=user_id),
            ),
        )
