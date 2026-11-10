"""Every column that holds personal data, and what an export does with it.

Pure policy data, and the one file in this package over 300 lines on
purpose: one reviewable entry per column, so whoever signs off a
withholding decision reads it — and its reason, which is the sentence
the subject is shown — in one place. Splitting by table group would
scatter the review the coverage tests in `tests/test_subject_access.py`
depend on.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.services.subject_access.schemas import ColumnClassification, DEAD, INCLUDED

# Every column of every table that holds personal data, and what the
# export does with it.
#
# `included`  — the export returns it, or matches on it to find the row
# `withheld`  — personal, and deliberately not disclosed. The report says
#               the column exists and why its value is missing
# `dead`      — in a table no code references. Recorded so the decision is
#               visible, and so an export cannot silently ignore rows that
#               do still exist
#
# Columns of these same tables that say nothing about any person live in
# `NOT_PERSONAL_COLUMNS`, in `not_personal_columns.py`. **Between the
# two, every column of every table here is classified**, and `tests/test_subject_access.py` proves it
# against the live database. That is the rule F115 built and F113 asked
# for: a table known to hold personal data holds it in all its columns
# until somebody says otherwise, column by column, in writing.
PERSONAL_DATA_COLUMNS: Mapping[str, ColumnClassification] = MappingProxyType(
    {
        # ── The account itself ────────────────────────────────────────────
        'users.user_id': INCLUDED,
        'users.email': INCLUDED,
        'users.full_name': INCLUDED,
        'users.auth_provider': INCLUDED,
        'users.created_at': INCLUDED,
        'users.updated_at': INCLUDED,
        'users.last_sign_in_at': INCLUDED,
        # B5/DEC-10. When an operator last withdrew this person's sessions.
        # Included: it is a fact about them and about an action taken
        # against their account, and a subject-access report that hid it
        # would be hiding the most consequential thing an administrator has
        # ever done to them.
        'users.tokens_valid_after': INCLUDED,
        # When they were deprovisioned. Included for the same reason and
        # more so: it is the fact that ended their access, and a report
        # that returned everything about somebody except why they can no
        # longer get in would be answering around the question.
        'users.deactivated_at': INCLUDED,
        # Whether they own the install. Included: it is a role held by this
        # person, and what they may do to everybody else's organisations
        # follows from it.
        'users.is_super_admin': INCLUDED,
        'users.hashed_password': ColumnClassification.withheld(
            'A password hash, excluded deliberately. It is not meaningful '
            'personal data and disclosing it would help an attacker who '
            'persuaded an administrator to run this export.'
        ),
        # ── The deployment-wide role record ───────────────────────────────
        'user_roles.user_id': INCLUDED,
        'user_roles.email': INCLUDED,
        'user_roles.full_name': INCLUDED,
        'user_roles.pending_email': INCLUDED,
        'user_roles.role': INCLUDED,
        'user_roles.status': INCLUDED,
        'user_roles.assigned_by': INCLUDED,
        'user_roles.created_at': INCLUDED,
        'user_roles.updated_at': INCLUDED,
        'user_roles.email_verification_token': ColumnClassification.withheld(
            'A single-use token, excluded for the same reason.'
        ),
        # ── Membership and access ─────────────────────────────────────────
        #
        # Which organisation and group a person belongs to is data
        # about them, not just about the organisation.
        'org_memberships.user_id': INCLUDED,
        'org_memberships.org_id': INCLUDED,
        'org_memberships.role': INCLUDED,
        'org_memberships.status': INCLUDED,
        'org_memberships.invited_email': INCLUDED,
        'org_memberships.invited_by': INCLUDED,
        'org_memberships.created_at': INCLUDED,
        'org_memberships.updated_at': INCLUDED,
        'organizations.owner_user_id': INCLUDED,
        'organizations.id': INCLUDED,
        'organizations.slug': INCLUDED,
        'organizations.display_name': INCLUDED,
        'organizations.created_at': INCLUDED,
        'org_group_members.user_id': INCLUDED,
        'org_group_members.group_id': INCLUDED,
        'org_group_members.added_by_user_id': INCLUDED,
        'org_group_members.added_at': INCLUDED,
        # ── Their own activity ────────────────────────────────────────────
        'audit_events.actor_user_id': INCLUDED,
        'audit_events.actor_email': INCLUDED,
        'audit_events.occurred_at': INCLUDED,
        'audit_events.method': INCLUDED,
        'audit_events.route': INCLUDED,
        'audit_events.target_type': INCLUDED,
        'audit_events.target_id': INCLUDED,
        'audit_events.status_code': INCLUDED,
        'audit_events.outcome': INCLUDED,
        # Their address, twice over: `client_host` is the connecting peer and
        # `forwarded_for` is what a proxy said it was. An export that returned
        # one and not the other would omit the address that is actually theirs
        # on any deployment behind a load balancer — which is every
        # deployment this chart produces.
        'audit_events.client_host': INCLUDED,
        'audit_events.forwarded_for': INCLUDED,
        'audit_events.user_agent': INCLUDED,
        # The one audit column that cannot be returned. `detail` is
        # per-route JSON, and on some routes it describes **somebody else**:
        # an administrator's erasure row carries the masked address of the
        # person erased, and an access-review row carries the member reviewed.
        # Returning an admin's own audit rows whole would therefore disclose
        # third parties in the act of answering the admin's request — the same
        # constraint the webhook section solves by filtering. Withheld rather
        # than filtered because the shape differs per route, so there is no
        # single rule for what to strip, and a wrong guess discloses.
        'audit_events.detail': ColumnClassification.withheld(
            'Per-route detail attached to your audit records. Withheld because on '
            'some routes it describes a third party rather than you — an erasure '
            'you performed carries the address of the person erased, an access '
            'review carries the member reviewed. The shape differs per route, so '
            'there is no single rule for what to remove, and returning it would '
            "disclose other people's data in the act of answering your request. "
            'Ask the controller if you need a specific record explained.'
        ),
        # Which super admin approved or revoked a registered service. A
        # decision about the deployment, already in the deployment audit
        # trail with the person's own activity above, which is where an
        # Article 15 answer returns it.
        'registered_clients.decided_by_user_id': ColumnClassification.withheld(
            'Which super admin approved or revoked a service registered with this '
            'deployment. The same decision is in your own audit activity above.'
        ),
        # Which super admin set the priority order of extensions. The same
        # change is in the deployment audit trail.
        'extension_priority.set_by_user_id': ColumnClassification.withheld(
            'Which super admin set the priority order of extensions, which decides '
            'which of two has a contested part of igni. The same change is in your '
            'own audit activity above.'
        ),
        # Who set the platform's caps on VM profiles, or an organisation's
        # quota of awake VM time. Decisions about the deployment, in its
        # audit trail with the person's own activity.
        'platform_vm_caps.updated_by_user_id': ColumnClassification.withheld(
            "Which super admin set the platform's caps on VM profiles. The same "
            'change is in your own audit activity above.'
        ),
        'org_vm_quotas.updated_by_user_id': ColumnClassification.withheld(
            "Which super admin set an organisation's quota of awake VM time. The "
            'same change is in your own audit activity above.'
        ),
        'platform_space_caps.updated_by_user_id': ColumnClassification.withheld(
            "Which super admin set the platform's caps on apps' spaces. The same "
            'change is in your own audit activity above.'
        ),
        'org_space_quotas.updated_by_user_id': ColumnClassification.withheld(
            "Which admin set an organisation's limits on apps' spaces. The same "
            'change is in your own audit activity above.'
        ),
        # An app's own name for one of its users (a space, §17). It names
        # somebody of the app's, never an igni account, so no export about
        # an igni person reaches it: the app answers for its users.
        'app_spaces.external_id': ColumnClassification.withheld(
            "An app's own name for one of its users. It never names an igni "
            "account, so nothing of yours is in it; the app's operator answers for "
            'its users.'
        ),
        'chat_usage.user_id': INCLUDED,
        'chat_usage.org_id': INCLUDED,
        'chat_usage.model': INCLUDED,
        'chat_usage.input_tokens': INCLUDED,
        'chat_usage.output_tokens': INCLUDED,
        'chat_usage.total_tokens': INCLUDED,
        'chat_usage.duration': INCLUDED,
        'chat_usage.stream': INCLUDED,
        'chat_usage.created_at': INCLUDED,
        # ── Things they created or configured ─────────────────────────────
        #
        # Provenance rather than data about them, but Article 15 covers "data
        # concerning" the subject and their authorship of a record is that.
        #
        # What is *in* the thing they made is not. A group entry's `content`
        # is a prompt or a script — organisational configuration that happens
        # to have an author — so the personal facts are that this person made
        # this named thing at this time, and those are what the export
        # returns. Returning the configuration itself would turn an Article 15
        # answer into a config dump and would disclose the deployment's
        # internals to whoever asks nicely.
        # Apps they registered or own, and the extensions they switched on
        # for an app.
        'org_apps.owner_user_id': INCLUDED,
        'org_apps.created_by_user_id': INCLUDED,
        'org_apps.created_at': INCLUDED,
        'org_app_extensions.enabled_by_user_id': INCLUDED,
        'org_app_extensions.app_id': INCLUDED,
        'org_app_extensions.extension_id': INCLUDED,
        'org_app_extensions.enabled_at': INCLUDED,
        # VM profiles they defined or changed: which, and when.
        'vm_profiles.created_by_user_id': INCLUDED,
        'vm_profiles.updated_by_user_id': INCLUDED,
        'vm_profiles.name': INCLUDED,
        'vm_profiles.created_at': INCLUDED,
        'vm_profiles.updated_at': INCLUDED,
        # A VM profile request they decided: which, what became of it, when.
        'vm_profile_requests.decided_by_user_id': INCLUDED,
        'vm_profile_requests.subject_kind': INCLUDED,
        'vm_profile_requests.subject_id': INCLUDED,
        'vm_profile_requests.state': INCLUDED,
        'vm_profile_requests.decided_at': INCLUDED,
        # Their own words, when they gave a reason for saying no.
        'vm_profile_requests.reason': INCLUDED,
        # A volume an admin grew: whose VM, and who grew it.
        'producer_volumes.producer_kind': INCLUDED,
        'producer_volumes.producer_id': INCLUDED,
        'producer_volumes.gib': INCLUDED,
        'producer_volumes.grown_by_kind': INCLUDED,
        'producer_volumes.grown_by_id': INCLUDED,
        'producer_volumes.grown_as': INCLUDED,
        'producer_volumes.grown_at': INCLUDED,
        # Commands they asked for, or that ran in their own VM, and what
        # came of them — the output is their VM's.
        'vm_commands.requested_by_kind': INCLUDED,
        'vm_commands.requested_by_id': INCLUDED,
        'vm_commands.requested_as': INCLUDED,
        'vm_commands.producer_kind': INCLUDED,
        'vm_commands.producer_id': INCLUDED,
        'vm_commands.command': INCLUDED,
        'vm_commands.state': INCLUDED,
        'vm_commands.created_at': INCLUDED,
        'vm_commands.started_at': INCLUDED,
        'vm_commands.finished_at': INCLUDED,
        'vm_commands.exit_code': INCLUDED,
        'vm_commands.output': INCLUDED,
        'vm_commands.error': INCLUDED,
        # Jobs for many VMs they queued or cancelled.
        'vm_jobs.requested_by_kind': INCLUDED,
        'vm_jobs.requested_by_id': INCLUDED,
        'vm_jobs.requested_as': INCLUDED,
        'vm_jobs.command': INCLUDED,
        'vm_jobs.created_at': INCLUDED,
        'vm_jobs.cancelled_at': INCLUDED,
        'vm_jobs.cancelled_by_id': INCLUDED,
        # Their cloud VM's credentials: when each was issued and last used,
        # and why it stopped. Never the secret — only hashes are kept.
        'vm_credentials.producer_kind': INCLUDED,
        'vm_credentials.producer_id': INCLUDED,
        'vm_credentials.created_by_kind': INCLUDED,
        'vm_credentials.created_by_id': INCLUDED,
        'vm_credentials.created_at': INCLUDED,
        'vm_credentials.last_used_at': INCLUDED,
        'vm_credentials.revoked_at': INCLUDED,
        'vm_credentials.revoked_reason': INCLUDED,
        'vm_credentials.revoked_by_id': INCLUDED,
        'vm_credentials.secret_hash': ColumnClassification.withheld(
            "Hashes of your cloud VM's credentials. A hash cannot be used to "
            'sign in, but it is a credential check, not information about you.'
        ),
        'vm_credentials.previous_hashes': ColumnClassification.withheld(
            "Hashes of your cloud VM's earlier credentials, kept to notice one "
            'being reused. Withheld for the same reason.'
        ),
        # Their personal API keys: what they called each, when it was made,
        # last used, expires, and why it stopped. Never the key — only a hash
        # of its secret is kept.
        'api_keys.user_id': INCLUDED,
        'api_keys.name': INCLUDED,
        'api_keys.created_at': INCLUDED,
        'api_keys.expires_at': INCLUDED,
        'api_keys.last_used_at': INCLUDED,
        'api_keys.revoked_at': INCLUDED,
        'api_keys.revoked_reason': INCLUDED,
        'api_keys.revoked_by_id': INCLUDED,
        'api_keys.secret_hash': ColumnClassification.withheld(
            'Hashes of your API keys. A hash cannot be used to act as you, but '
            'it is a credential check, not information about you.'
        ),
        # Which MCP servers they approved, from which configuration, when.
        'person_mcp_approvals.user_id': INCLUDED,
        'person_mcp_approvals.server_name': INCLUDED,
        'person_mcp_approvals.source': INCLUDED,
        'person_mcp_approvals.approved_at': INCLUDED,
        # Events services asked to deliver to an extension for them: which
        # extension, what happened, and whether it reached them.
        'producer_events.producer_kind': INCLUDED,
        'producer_events.producer_id': INCLUDED,
        'producer_events.extension_id': INCLUDED,
        'producer_events.event_type': INCLUDED,
        'producer_events.payload': INCLUDED,
        'producer_events.state': INCLUDED,
        'producer_events.created_at': INCLUDED,
        'producer_events.delivered_at': INCLUDED,
        'producer_events.failed_reason': INCLUDED,
        # What a service sent their agent (§15): the kind, the chat's thread
        # and title, the text, what an action did, the files it named, and
        # why an event went not handled.
        'producer_events.kind': INCLUDED,
        'producer_events.thread': INCLUDED,
        'producer_events.text': INCLUDED,
        'producer_events.title': INCLUDED,
        'producer_events.start_run': INCLUDED,
        'producer_events.action': INCLUDED,
        'producer_events.attachment_ids': INCLUDED,
        'producer_events.not_handled_reason': INCLUDED,
        # The files a service sent them, described. The bytes are withheld:
        # they were the service's to send, are kept at most 14 days, and the
        # producer's igni holds the copy it imported into the chat.
        'service_attachments.event_id': INCLUDED,
        'service_attachments.filename': INCLUDED,
        'service_attachments.content_type': INCLUDED,
        'service_attachments.size': INCLUDED,
        'service_attachments.sha256': INCLUDED,
        'service_attachments.created_at': INCLUDED,
        'service_attachments.expires_at': INCLUDED,
        'service_attachments.content': ColumnClassification.withheld(
            'The bytes of files a service sent your agent. They are described '
            "above; the bytes were the service's to send, are kept at most 14 "
            'days, and the copy imported into your chat is in your igni.'
        ),
        # What the cloud's gateway was last told about their VM: whether they
        # are a producer still, and when that changed.
        'producer_feed.producer_kind': INCLUDED,
        'producer_feed.producer_id': INCLUDED,
        'producer_feed.active': INCLUDED,
        'producer_feed.changed_at': INCLUDED,
        'producer_feed.removed': INCLUDED,
        'producer_feed.removed_at': INCLUDED,
        'producer_feed.erase_at': INCLUDED,
        'producer_feed.seen_removed_at': INCLUDED,
        'producer_feed.erased': INCLUDED,
        'producer_feed.legal_hold': INCLUDED,
        'producer_feed.erasure_done_at': INCLUDED,
        'producer_feed.discarded_by_user_id': INCLUDED,
        'producer_feed.discarded_at': INCLUDED,
        # A legal hold on them, or one they placed.
        'legal_holds.user_id': INCLUDED,
        'legal_holds.reason': INCLUDED,
        'legal_holds.placed_by_user_id': INCLUDED,
        'legal_holds.placed_at': INCLUDED,
        # That their tokens were revoked, and why, for the gateway and services.
        'revocations.subject_id': INCLUDED,
        'revocations.reason': INCLUDED,
        'revocations.created_at': INCLUDED,
        # An export of a removed member's data an admin asked for: whose,
        # who asked, and what became of it. The archive itself is withheld.
        'producer_exports.producer_kind': INCLUDED,
        'producer_exports.producer_id': INCLUDED,
        'producer_exports.requested_by_user_id': INCLUDED,
        'producer_exports.requested_at': INCLUDED,
        'producer_exports.state': INCLUDED,
        'producer_exports.finished_at': INCLUDED,
        'producer_exports.content_oid': ColumnClassification.withheld(
            'An archive of your data in an organisation you were removed from, '
            'exported for that organisation, which controls it, during its '
            'retention. Ask the organisation for it; it is deleted when it expires.'
        ),
        'platform_retention.updated_by_user_id': ColumnClassification.withheld(
            "Which super admin set the platform's longest retention of removed "
            'members. The same change is in your own audit activity above.'
        ),
        'org_retention.updated_by_user_id': ColumnClassification.withheld(
            "Which admin set an organisation's retention of removed members. The "
            'same change is in your own audit activity above.'
        ),
        'app_legal_holds.reason': INCLUDED,
        'app_legal_holds.placed_by_user_id': INCLUDED,
        'app_legal_holds.placed_at': INCLUDED,
        # A change waiting to be numbered into the feed: deleted within
        # seconds, at the gateway's next poll. Nothing about them the feed
        # row does not already say.
        'producer_feed_marks.producer_id': ColumnClassification.withheld(
            "That something about you changed and the cloud's gateway has yet to "
            'be told: kept for seconds, until its next poll, and saying nothing the '
            "gateway's feed above does not."
        ),
        'org_groups.created_by_user_id': INCLUDED,
        'org_groups.name': INCLUDED,
        'org_groups.created_at': INCLUDED,
        'org_group_entries.created_by_user_id': INCLUDED,
        'org_group_entries.created_at': INCLUDED,
        'org_group_entries.entry_name': INCLUDED,
        # Switching an extension on for a group is something an admin did:
        # which group, which extension, when.
        'org_group_extensions.enabled_by_user_id': INCLUDED,
        'org_group_extensions.group_id': INCLUDED,
        'org_group_extensions.extension_id': INCLUDED,
        'org_group_extensions.enabled_at': INCLUDED,
        # ── What they were told ──────────────────────────────────────
        # A notification is a record of somebody being informed, which is
        # a fact about them whichever end of it they were on.
        #
        # ``user_id`` is who was told: theirs outright, and the only
        # record of what this deployment decided they needed to know.
        #
        # ``actor_user_id`` and ``actor_email`` are who did the thing. On
        # a notification addressed to *them* it names a third party, and
        # it is still included, because who acted on them is part of
        # what happened to them. "Sam added you to a group" is
        # a fact about the subject, and an export that redacted Sam would
        # answer "somebody commented" and leave them unable to check
        # whether that was true. What is not included is the reverse
        # direction: rows where they are only the actor are somebody
        # else's queue, and appear in that person's export rather than in
        # theirs.
        'notifications.user_id': INCLUDED,
        'notifications.actor_user_id': INCLUDED,
        'notifications.actor_email': INCLUDED,
        # What they were told, in the words they were told it, and which
        # extension told them. The verb, the line they read and where it
        # led are the notification — an export listing that they were
        # notified fourteen times without saying of what would be a worse
        # answer than none. ``target`` is included for the same reason:
        # it is the place the line pointed them, as the service put it.
        'notifications.source': INCLUDED,
        'notifications.kind': INCLUDED,
        'notifications.title': INCLUDED,
        'notifications.body': INCLUDED,
        'notifications.target': INCLUDED,
        'notifications.created_at': INCLUDED,
        # Whether their machine received it and whether they saw it. Facts
        # about what this person has done, and the only record of them.
        'notifications.delivered_at': INCLUDED,
        'notifications.read_at': INCLUDED,
        'inference_nodes.created_by_user_id': INCLUDED,
        'inference_nodes.created_at': INCLUDED,
        # ── A table nothing reads ─────────────────────────────────────────
        #
        # `org_invitations` is referenced by no code outside its own model — a
        # table the invitation flow moved off of and left behind. Every column
        # is marked rather than the two that look personal, because the
        # decision being recorded is about the table.
        'org_invitations.id': DEAD,
        'org_invitations.org_id': DEAD,
        'org_invitations.email': DEAD,
        'org_invitations.role': DEAD,
        'org_invitations.token': DEAD,
        'org_invitations.invited_by': DEAD,
        'org_invitations.status': DEAD,
        'org_invitations.expires_at': DEAD,
        'org_invitations.created_at': DEAD,
    }
)
