"""Answering a subject access request. GDPR Article 15.

DP-8 in `COMPLIANCE_TRACKER.md`. Before this, a customer receiving a
request had one option: write SQL across every table that holds personal
data, which is not a plan — it is a promise to miss something.

## Article 15 is not a database dump

It grants a copy of the personal data **plus** the context: the purposes
of processing, the categories of data, the recipients, the retention
period and the source. A report that returns rows and no context answers
half the article, so `describe_processing` builds the other half from what
the deployment is actually configured to do.

## Two hazards, and they pull in opposite directions

**Never return a credential.** `users.hashed_password` and
`user_roles.email_verification_token` are deliberately excluded. A
password hash is not the subject's personal data in any useful sense, and
handing one to whoever persuades an administrator to run an export is a
gift to an attacker. The report says the columns exist and were withheld,
because silently omitting them would make the export look complete when it
is not.

**Never disclose a third party while complying.** Audit detail on some
routes describes somebody else — the person an erasure was run for, the
member an access review covered — so it is withheld, and the report says
why.

## Coverage is asserted, not assumed

Three rules in `tests/test_subject_access.py`, each catching what the
others cannot:

* **discovery** — any column in the live schema whose *name* references a
  person (`%user_id%`, `%email%`, `%_by%`, `%actor%`) must be classified.
  A migration adding a table with a `user_id` fails until somebody
  decides;
* **depth** — once a table appears here, **every one of its columns** must
  be classified, in `PERSONAL_DATA_COLUMNS` or in
  `NOT_PERSONAL_COLUMNS`. This is the rule F113 asked for and the one
  discovery cannot do: nothing about `full_name`, `use_case` or
  `use_case` looks like an identifier, and the schema had four such
  columns unmapped when it was built;
* **behaviour** — every column marked `included` must actually be returned
  by, or matched on by, **each** query that reads its table. Otherwise the
  classification is a claim about the export rather than a description of
  it, which is worse than no classification: it reads as assurance.

Every column of every table judged in writing — more than anybody holds
in their head, and the schema grows.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from pydantic import BaseModel

from app.config import settings
from app.db.sql.subject_access import ACTIVITY_LIMIT, SubjectAccessDB
from app.services.email.delivery import EmailDelivery
from app.services.identity.service_subjects import ServiceSubjects
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)


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
# `NOT_PERSONAL_COLUMNS` below. **Between the two, every column of every
# table here is classified**, and `tests/test_subject_access.py` proves it
# against the live database. That is the rule F115 built and F113 asked
# for: a table known to hold personal data holds it in all its columns
# until somebody says otherwise, column by column, in writing.
PERSONAL_DATA_COLUMNS: dict[str, str] = {
    # ── The account itself ────────────────────────────────────────────
    'users.user_id': 'included',
    'users.email': 'included',
    'users.full_name': 'included',
    'users.auth_provider': 'included',
    'users.created_at': 'included',
    'users.updated_at': 'included',
    'users.last_sign_in_at': 'included',
    # B5/DEC-10. When an operator last withdrew this person's sessions.
    # Included: it is a fact about them and about an action taken
    # against their account, and a subject-access report that hid it
    # would be hiding the most consequential thing an administrator has
    # ever done to them.
    'users.tokens_valid_after': 'included',
    # When they were deprovisioned. Included for the same reason and
    # more so: it is the fact that ended their access, and a report
    # that returned everything about somebody except why they can no
    # longer get in would be answering around the question.
    'users.deactivated_at': 'included',
    # Whether they own the install. Included: it is a role held by this
    # person, and what they may do to everybody else's organisations
    # follows from it.
    'users.is_super_admin': 'included',
    'users.hashed_password': 'withheld',
    # ── The deployment-wide role record ───────────────────────────────
    'user_roles.user_id': 'included',
    'user_roles.email': 'included',
    'user_roles.full_name': 'included',
    'user_roles.pending_email': 'included',
    'user_roles.role': 'included',
    'user_roles.status': 'included',
    'user_roles.assigned_by': 'included',
    'user_roles.created_at': 'included',
    'user_roles.updated_at': 'included',
    'user_roles.email_verification_token': 'withheld',
    # ── Membership and access ─────────────────────────────────────────
    #
    # Which organisation and group a person belongs to is data
    # about them, not just about the organisation.
    'org_memberships.user_id': 'included',
    'org_memberships.org_id': 'included',
    'org_memberships.role': 'included',
    'org_memberships.status': 'included',
    'org_memberships.invited_email': 'included',
    'org_memberships.invited_by': 'included',
    'org_memberships.created_at': 'included',
    'org_memberships.updated_at': 'included',
    'organizations.owner_user_id': 'included',
    'organizations.id': 'included',
    'organizations.slug': 'included',
    'organizations.display_name': 'included',
    'organizations.created_at': 'included',
    'org_group_members.user_id': 'included',
    'org_group_members.group_id': 'included',
    'org_group_members.added_by_user_id': 'included',
    'org_group_members.added_at': 'included',
    # ── Their own activity ────────────────────────────────────────────
    'audit_events.actor_user_id': 'included',
    'audit_events.actor_email': 'included',
    'audit_events.occurred_at': 'included',
    'audit_events.method': 'included',
    'audit_events.route': 'included',
    'audit_events.target_type': 'included',
    'audit_events.target_id': 'included',
    'audit_events.status_code': 'included',
    'audit_events.outcome': 'included',
    # Their address, twice over: `client_host` is the connecting peer and
    # `forwarded_for` is what a proxy said it was. An export that returned
    # one and not the other would omit the address that is actually theirs
    # on any deployment behind a load balancer — which is every
    # deployment this chart produces.
    'audit_events.client_host': 'included',
    'audit_events.forwarded_for': 'included',
    'audit_events.user_agent': 'included',
    # The one audit column that cannot be returned. `detail` is
    # per-route JSON, and on some routes it describes **somebody else**:
    # an administrator's erasure row carries the masked address of the
    # person erased, and an access-review row carries the member reviewed.
    # Returning an admin's own audit rows whole would therefore disclose
    # third parties in the act of answering the admin's request — the same
    # constraint the webhook section solves by filtering. Withheld rather
    # than filtered because the shape differs per route, so there is no
    # single rule for what to strip, and a wrong guess discloses.
    'audit_events.detail': 'withheld',
    # Which super admin approved or revoked a registered service. A
    # decision about the deployment, already in the deployment audit
    # trail with the person's own activity above, which is where an
    # Article 15 answer returns it.
    'registered_clients.decided_by_user_id': 'withheld',
    # Which super admin set the priority order of extensions. The same
    # change is in the deployment audit trail.
    'extension_priority.set_by_user_id': 'withheld',
    # Who set the platform's caps on VM profiles, or an organisation's
    # quota of awake VM time. Decisions about the deployment, in its
    # audit trail with the person's own activity.
    'platform_vm_caps.updated_by_user_id': 'withheld',
    'org_vm_quotas.updated_by_user_id': 'withheld',
    'platform_space_caps.updated_by_user_id': 'withheld',
    'org_space_quotas.updated_by_user_id': 'withheld',
    # An app's own name for one of its users (a space, §17). It names
    # somebody of the app's, never an igni account, so no export about
    # an igni person reaches it: the app answers for its users.
    'app_spaces.external_id': 'withheld',
    'chat_usage.user_id': 'included',
    'chat_usage.org_id': 'included',
    'chat_usage.model': 'included',
    'chat_usage.input_tokens': 'included',
    'chat_usage.output_tokens': 'included',
    'chat_usage.total_tokens': 'included',
    'chat_usage.duration': 'included',
    'chat_usage.stream': 'included',
    'chat_usage.created_at': 'included',
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
    'org_apps.owner_user_id': 'included',
    'org_apps.created_by_user_id': 'included',
    'org_apps.created_at': 'included',
    'org_app_extensions.enabled_by_user_id': 'included',
    'org_app_extensions.app_id': 'included',
    'org_app_extensions.extension_id': 'included',
    'org_app_extensions.enabled_at': 'included',
    # VM profiles they defined or changed: which, and when.
    'vm_profiles.created_by_user_id': 'included',
    'vm_profiles.updated_by_user_id': 'included',
    'vm_profiles.name': 'included',
    'vm_profiles.created_at': 'included',
    'vm_profiles.updated_at': 'included',
    # A volume an admin grew: whose VM, and who grew it.
    'producer_volumes.producer_kind': 'included',
    'producer_volumes.producer_id': 'included',
    'producer_volumes.gib': 'included',
    'producer_volumes.grown_by_kind': 'included',
    'producer_volumes.grown_by_id': 'included',
    'producer_volumes.grown_as': 'included',
    'producer_volumes.grown_at': 'included',
    # Commands they asked for, or that ran in their own VM, and what
    # came of them — the output is their VM's.
    'vm_commands.requested_by_kind': 'included',
    'vm_commands.requested_by_id': 'included',
    'vm_commands.requested_as': 'included',
    'vm_commands.producer_kind': 'included',
    'vm_commands.producer_id': 'included',
    'vm_commands.command': 'included',
    'vm_commands.state': 'included',
    'vm_commands.created_at': 'included',
    'vm_commands.started_at': 'included',
    'vm_commands.finished_at': 'included',
    'vm_commands.exit_code': 'included',
    'vm_commands.output': 'included',
    'vm_commands.error': 'included',
    # Jobs for many VMs they queued or cancelled.
    'vm_jobs.requested_by_kind': 'included',
    'vm_jobs.requested_by_id': 'included',
    'vm_jobs.requested_as': 'included',
    'vm_jobs.command': 'included',
    'vm_jobs.created_at': 'included',
    'vm_jobs.cancelled_at': 'included',
    'vm_jobs.cancelled_by_id': 'included',
    # Their cloud VM's credentials: when each was issued and last used,
    # and why it stopped. Never the secret — only hashes are kept.
    'vm_credentials.producer_kind': 'included',
    'vm_credentials.producer_id': 'included',
    'vm_credentials.created_by_kind': 'included',
    'vm_credentials.created_by_id': 'included',
    'vm_credentials.created_at': 'included',
    'vm_credentials.last_used_at': 'included',
    'vm_credentials.revoked_at': 'included',
    'vm_credentials.revoked_reason': 'included',
    'vm_credentials.revoked_by_id': 'included',
    'vm_credentials.secret_hash': 'withheld',
    'vm_credentials.previous_hashes': 'withheld',
    # Which MCP servers they approved, from which configuration, when.
    'person_mcp_approvals.user_id': 'included',
    'person_mcp_approvals.server_name': 'included',
    'person_mcp_approvals.source': 'included',
    'person_mcp_approvals.approved_at': 'included',
    # Events services asked to deliver to an extension for them: which
    # extension, what happened, and whether it reached them.
    'producer_events.producer_kind': 'included',
    'producer_events.producer_id': 'included',
    'producer_events.extension_id': 'included',
    'producer_events.event_type': 'included',
    'producer_events.payload': 'included',
    'producer_events.state': 'included',
    'producer_events.created_at': 'included',
    'producer_events.delivered_at': 'included',
    'producer_events.failed_reason': 'included',
    # What a service sent their agent (§15): the kind, the chat's thread
    # and title, the text, what an action did, the files it named, and
    # why an event went not handled.
    'producer_events.kind': 'included',
    'producer_events.thread': 'included',
    'producer_events.text': 'included',
    'producer_events.title': 'included',
    'producer_events.start_run': 'included',
    'producer_events.action': 'included',
    'producer_events.attachment_ids': 'included',
    'producer_events.not_handled_reason': 'included',
    # The files a service sent them, described. The bytes are withheld:
    # they were the service's to send, are kept at most 14 days, and the
    # producer's igni holds the copy it imported into the chat.
    'service_attachments.event_id': 'included',
    'service_attachments.filename': 'included',
    'service_attachments.content_type': 'included',
    'service_attachments.size': 'included',
    'service_attachments.sha256': 'included',
    'service_attachments.created_at': 'included',
    'service_attachments.expires_at': 'included',
    'service_attachments.content': 'withheld',
    # What the cloud's gateway was last told about their VM: whether they
    # are a producer still, and when that changed.
    'producer_feed.producer_kind': 'included',
    'producer_feed.producer_id': 'included',
    'producer_feed.active': 'included',
    'producer_feed.changed_at': 'included',
    'producer_feed.removed': 'included',
    'producer_feed.removed_at': 'included',
    'producer_feed.erase_at': 'included',
    # A legal hold on them, or one they placed.
    'legal_holds.user_id': 'included',
    # That their tokens were revoked, and why, for the gateway and services.
    'revocations.subject_id': 'included',
    'revocations.reason': 'included',
    'revocations.created_at': 'included',
    # An export of a removed member's data an admin asked for: whose,
    # who asked, and what became of it. The archive itself is withheld.
    'producer_exports.producer_kind': 'included',
    'producer_exports.producer_id': 'included',
    'producer_exports.requested_by_user_id': 'included',
    'producer_exports.requested_at': 'included',
    'producer_exports.state': 'included',
    'producer_exports.finished_at': 'included',
    'producer_exports.content_oid': 'withheld',
    'legal_holds.reason': 'included',
    'legal_holds.placed_by_user_id': 'included',
    'legal_holds.placed_at': 'included',
    'platform_retention.updated_by_user_id': 'withheld',
    'org_retention.updated_by_user_id': 'withheld',
    'producer_feed.seen_removed_at': 'included',
    'producer_feed.erased': 'included',
    # A change waiting to be numbered into the feed: deleted within
    # seconds, at the gateway's next poll. Nothing about them the feed
    # row does not already say.
    'producer_feed_marks.producer_id': 'withheld',
    'org_groups.created_by_user_id': 'included',
    'org_groups.name': 'included',
    'org_groups.created_at': 'included',
    'org_group_entries.created_by_user_id': 'included',
    'org_group_entries.created_at': 'included',
    'org_group_entries.entry_name': 'included',
    # Switching an extension on for a group is something an admin did:
    # which group, which extension, when.
    'org_group_extensions.enabled_by_user_id': 'included',
    'org_group_extensions.group_id': 'included',
    'org_group_extensions.extension_id': 'included',
    'org_group_extensions.enabled_at': 'included',
    # ── What they were told ──────────────────────────────────────
    # A notification is a record of somebody being informed, which is
    # a fact about them whichever end of it they were on.
    #
    # ``user_id`` is who was told: theirs outright, and the only
    # record of what this deployment decided they needed to know.
    #
    # ``actor_user_id`` and ``actor_email`` are who did the thing. On
    # a notification addressed to *them* it names a third party, and
    # it is still included — the same reasoning as
    # ``asked_by_user_id`` above. "Sam commented on your document" is
    # a fact about the subject, and an export that redacted Sam would
    # answer "somebody commented" and leave them unable to check
    # whether that was true. What is not included is the reverse
    # direction: rows where they are only the actor are somebody
    # else's queue, and appear in that person's export rather than in
    # theirs.
    'notifications.user_id': 'included',
    'notifications.actor_user_id': 'included',
    'notifications.actor_email': 'included',
    # What they were told, in the words they were told it, and which
    # extension told them. The verb, the line they read and where it
    # led are the notification — an export listing that they were
    # notified fourteen times without saying of what would be a worse
    # answer than none. ``target`` is included for the same reason:
    # it is the place the line pointed them, as the service put it.
    'notifications.source': 'included',
    'notifications.kind': 'included',
    'notifications.title': 'included',
    'notifications.body': 'included',
    'notifications.target': 'included',
    'notifications.created_at': 'included',
    # Whether their machine received it and whether they saw it. Facts
    # about what this person has done, and the only record of them.
    'notifications.delivered_at': 'included',
    'notifications.read_at': 'included',
    'inference_nodes.created_by_user_id': 'included',
    'inference_nodes.created_at': 'included',
    # A GitHub App can be installed on a *personal* account, in which case
    # the login is a person's username and the numeric id identifies them
    # across renames. `account_type` is what says which of the two this
    # is, so it is returned with them rather than left for the reader to
    # infer.
    # The primary key is the OAuth `state` nonce — a single-use bearer of
    # this person's pending install. Withheld for the reason
    # `email_verification_token` is: an unconsumed one is live credential
    # material, and an export is not the place to hand one out.
    # ── A table nothing reads ─────────────────────────────────────────
    #
    # `org_invitations` is referenced by no code outside its own model — a
    # table the invitation flow moved off of and left behind. Every column
    # is marked rather than the two that look personal, because the
    # decision being recorded is about the table.
    'org_invitations.id': 'dead',
    'org_invitations.org_id': 'dead',
    'org_invitations.email': 'dead',
    'org_invitations.role': 'dead',
    'org_invitations.token': 'dead',
    'org_invitations.invited_by': 'dead',
    'org_invitations.status': 'dead',
    'org_invitations.expires_at': 'dead',
    'org_invitations.created_at': 'dead',
}


# The rest of the columns of those same tables: the ones that say nothing
# about any person. Written out rather than inferred, because "this column
# is not personal data" is a judgement and F113 is what happens when it is
# made implicitly by a pattern match.
#
# Grouped by *why*, since the reason is the part worth reviewing.
NOT_PERSONAL_COLUMNS: frozenset[str] = frozenset(
    {
        # A registered service or app: its identity, its public keys,
        # where it is reached and whether it is approved. Describes
        # software, not a person; the one column naming somebody — who
        # decided — is classified above.
        'registered_clients.client_id',
        'registered_clients.kind',
        'registered_clients.name',
        'registered_clients.base_url',
        'registered_clients.redirect_uris',
        'registered_clients.jwks',
        'registered_clients.thumbprint',
        'registered_clients.state',
        'registered_clients.created_at',
        'registered_clients.decided_at',
        # The priority order of extensions, which settles their collisions.
        # Software; who set it is above.
        'extension_priority.extension_id',
        'extension_priority.position',
        'extension_priority.break_glass',
        'extension_priority.set_at',
        # Surrogate keys and row identifiers. They identify a record, and
        # the record's subject is named by other columns.
        'users.id',
        'user_roles.id',
        'org_memberships.id',
        'org_group_members.id',
        'org_groups.id',
        'org_group_entries.id',
        'org_group_extensions.id',
        'org_app_extensions.id',
        'org_apps.app_id',
        'vm_profiles.id',
        'vm_commands.id',
        'vm_commands.job_id',
        'vm_jobs.id',
        'person_mcp_approvals.id',
        # Which folder a row sits in. A surrogate reference to another
        # row, the same shape as ``artifact_comments.parent_id`` — the
        # arrangement it encodes is the person's, and it is answered
        # by exporting their folders alongside their documents rather
        # than by calling an opaque id their personal data.
        # And the other half of where a folder sits: under a document
        # rather than a folder. Same shape, same reasoning.
        # A notification's own id, and the registered service that
        # posted it. Each names a *record* or a piece of software — the
        # people involved are named by ``user_id`` and the actor
        # columns, which are classified above.
        'notifications.id',
        'notifications.source_client_id',
        # What happened, named by the service so it cannot be told
        # twice — it identifies an event, not a person.
        'notifications.event_key',
        # A draft's own identity and the document it belongs to.
        # Surrogate references, like every other id here.
        # The encoded document. Not a reference to a person and not
        # exportable to one — see the note beside
        # ``updated_by_user_id`` above.
        # Which document was asked about. A surrogate reference, the
        # same shape as every other id here: the question it encodes
        # is answered by exporting the request beside the documents,
        # not by calling an opaque id personal data.
        'inference_nodes.id',
        'audit_events.id',
        'chat_usage.id',
        'inference_nodes.node_id',
        'org_group_entries.group_id',
    }
    | {
        # Which organisation a record belongs to, where the record is not
        # about a person. `org_memberships.org_id` is personal and is in
        # the map above; an inference node's is not.
        'audit_events.org_id',
        'inference_nodes.org_id',
        'org_groups.org_id',
    }
    | {
        # Configuration. Describes what the deployment does, not who
        # anybody is. Several of these have an author, and the authorship
        # is recorded in `PERSONAL_DATA_COLUMNS` — the setting itself is
        # not that person's data.
        'org_groups.description',
        'org_groups.default_model',
        # Brand overrides: colours and a logo the org chose for itself.
        # An admin authored it and that authorship is recorded in
        # `created_by_user_id`; the palette is not that admin's data,
        # and it says nothing about the people who see it.
        'org_groups.theme',
        'org_groups.is_default',
        'org_groups.updated_at',
        'org_group_entries.kind',
        'org_group_entries.content',
        'org_group_entries.content_type',
        'org_group_entries.enabled',
        'org_group_entries.updated_at',
        'org_group_entries.source_url',
        'org_group_entries.source_ref',
        'org_group_entries.source_subdir',
        'org_group_entries.model',
        # Identifiers, not content. ``slug`` is the random public path,
        # ``artifact_id`` names a document in a store on the person's own
        # machine that this server never sees, ``kind`` is a free-form
        # label and ``version`` is a counter. The document itself — its
        # title, body and audience — is classified as theirs above.
        # Counters and offsets. ``version``/``n``/``base_n`` are
        # positions in a chain, ``resolved_in_n`` names the version
        # that applied a comment, and the quote offsets are a
        # highlighting hint — none of them says anything about a
        # person. ``artifact_id`` names a document in a store on the
        # person's own machine that this server never sees.
        'inference_nodes.base_url',
        'inference_nodes.models',
        'inference_nodes.max_concurrent',
        'inference_nodes.pools',
        'inference_nodes.multimodal',
        'inference_nodes.enabled',
        'inference_nodes.updated_at',
        'organizations.updated_at',
    }
    | {
        # Credentials belonging to a *system* — a GPU node's proxy key.
        # Not personal data
        # in any sense, and not `withheld` either: `withheld` means "this
        # person's, and we are not giving it to you", which would be the
        # wrong thing to tell them about a machine credential.
        'inference_nodes.api_key',
    }
    | {
        # The cloud's VMs: which profile a group or app has, what a
        # profile gives a VM, the platform's caps and an organisation's
        # quota, and how the node agent handled a command. Configuration
        # and plumbing; who decided or asked is classified above.
        'org_groups.vm_profile_id',
        'org_apps.vm_profile_id',
        'registered_clients.org_id',
        'vm_profiles.is_default',
        'vm_profiles.memory_mib',
        'vm_profiles.normal_memory_mib',
        'vm_profiles.vcpus',
        'vm_profiles.idle_before_pause_s',
        'vm_profiles.expected_memory_mib',
        'vm_profiles.packages',
        'vm_profiles.setup_script',
        'vm_commands.timeout_s',
        'vm_commands.wake',
        'vm_commands.handed_to',
        'vm_commands.handed_out_at',
        'vm_jobs.scope_kind',
        'producer_feed.org_id',
        'producer_feed.profile_version',
        'producer_feed.packages_version',
        'producer_feed.vm_profile',
        'producer_feed.seq',
        'producer_feed.owner_app_id',
        'producer_feed.incarnation',
        'chat_usage.app_id',
        'producer_feed.erase_after_days',
        'legal_holds.org_id',
        'revocations.seq',
        'revocations.kind',
        'revocations.org_id',
        'revocations.incarnation',
        'revocations.not_before_ms',
        'producer_exports.id',
        'producer_exports.incarnation',
        'producer_exports.handed_to',
        'producer_exports.handed_out_at',
        'producer_exports.error',
        'producer_exports.size',
        'producer_exports.expires_at',
        'platform_retention.id',
        'platform_retention.max_removed_member_retention_days',
        'platform_retention.updated_at',
        'org_retention.org_id',
        'org_retention.removed_member_retention_days',
        'org_retention.updated_at',
        'producer_feed.volume_gib',
        'producer_volumes.incarnation',
        'vm_profiles.volume_gib',
        'vm_profiles.volume_max_gib',
        'platform_vm_caps.max_volume_gib',
        'producer_feed_marks.org_id',
        'producer_feed_marks.producer_kind',
        'producer_feed_marks.n',
        'producer_events.id',
        'producer_events.client_id',
        'producer_events.event_key',
        'producer_events.expires_at',
        'producer_events.handed_out_at',
        'producer_events.dropped_reason',
        'service_attachments.id',
        'service_attachments.client_id',
        'vm_credentials.id',
        'vm_credentials.audience',
        'vm_credentials.generation',
        'vm_credentials.pending_pickup',
        'vm_credentials.picked_up_at',
        'vm_credentials.picked_up_by',
        'vm_credentials.rotated_at',
        'vm_credentials.retry_used',
        'producer_events.updated_at',
        'vm_jobs.scope_id',
        'vm_jobs.timeout_s',
        'platform_vm_caps.id',
        'platform_vm_caps.max_memory_mib',
        'platform_vm_caps.max_vcpus',
        'platform_vm_caps.max_idle_before_pause_s',
        'platform_vm_caps.updated_at',
        'org_vm_quotas.org_id',
        'org_vm_quotas.awake_vm_seconds_per_month',
        'org_vm_quotas.updated_at',
        'platform_space_caps.id',
        'platform_space_caps.max_spaces_per_app',
        'platform_space_caps.max_spaces_per_org',
        'platform_space_caps.updated_at',
        'org_space_quotas.org_id',
        'org_space_quotas.max_spaces_per_app',
        'org_space_quotas.max_spaces',
        'org_space_quotas.updated_at',
        'app_spaces.id',
        'app_spaces.app_id',
        'app_spaces.created_at',
    }
)


# Per-section caps. Real limits rather than tidy defaults: a very active
# person's full history needs pagination, which this does not have. The
# caps are therefore *reported* — an export that silently truncated would
# be the "no silent caps" failure, and here it would mean handing somebody
# an incomplete answer to a legal request while calling it their data.
# One constant each, shared with the queries that enforce them. The cap
# the query applies and the cap the disclosure checks against have to be
# the same number: if they drift, a section either reports a truncation
# that did not happen or stays silent about one that did — and the
# second is the failure this whole mechanism exists to prevent.
_ACTIVITY_LIMIT = ACTIVITY_LIMIT


def _note_if_capped(report: dict[str, Any], section: str, rows: list[Any], limit: int) -> None:
    """Record that a section hit its cap, in the report itself."""
    if len(rows) >= limit:
        report.setdefault('truncated', {})[section] = (
            f'{limit} rows returned, which is the maximum this export produces. '
            'There are more. Narrow the request or query the database directly '
            'for the complete set.'
        )


def _included_columns() -> set[str]:
    return {col for col, verdict in PERSONAL_DATA_COLUMNS.items() if verdict == 'included'}


def describe_processing() -> dict[str, Any]:
    """The Article 15 context, built from what this deployment is doing.

    Generated rather than boilerplate: retention comes from the settings
    actually in force, and the recipients section reflects the providers
    that are actually configured. A static paragraph would go stale and
    would be the same paragraph for a deployment with no email configured
    as for one relaying through SES.
    """
    recipients: list[str] = []
    # The same test the sending path uses, so the report cannot disagree
    # with what is sent. It used to check ``smtp_host`` alone, which left
    # out every deployment sending through the SES API (no host needed).
    if EmailDelivery().enabled:
        if settings.email_backend == 'ses':
            region = settings.ses_region or "the AWS SDK's default region"
            recipients.append(f'Amazon SES in {region}, for sign-in codes and invitations')
        else:
            recipients.append(f'the SMTP relay at {settings.smtp_host}, for sign-in codes and invitations')

    return {
        'controller': (
            'The organisation operating this igni deployment. igni is self-hosted: '
            'the software vendor holds none of this data and has no access to it.'
        ),
        'purposes': [
            'authenticating the person and deciding what they may access',
            'recording who performed privileged actions, for security review',
            'attributing group configuration to whoever made it',
            'accounting for model usage',
        ],
        'categories': [
            'identity and contact data (name, email address)',
            'authentication metadata (method, last sign-in)',
            'authorisation data (organisation membership, role, status)',
            'activity records (audit events, model usage)',
        ],
        'recipients': recipients
        or [
            'none. No email relay and no object store are configured, so nothing '
            'about this person is sent anywhere outside this deployment.'
        ],
        'retention': {
            'audit_events': (
                f'{settings.audit_retention_days} days'
                if settings.audit_retention_days > 0
                else 'kept indefinitely — no retention window is configured'
            ),
            'everything else': (
                'kept until the account or the organisation is deleted, or the person exercises erasure'
            ),
        },
        'source': (
            'Provided by the person when they signed up or accepted an invitation, '
            'or by an administrator who invited them.'
        ),
        'rights': (
            'Rectification and erasure are available. Documents can be redacted for '
            'a named person via POST /v1/portal/erasure (GDPR Art. 17).'
        ),
    }


class SubjectAccessReport(BaseModel):
    """What an Article 15 request is answered with.

    The envelope has a fixed key set and was a `dict[str, Any]` built
    up by assignment, returned straight out of an endpoint annotated
    the same way — so the one HTTP contract in this product that a
    regulator might read was described nowhere.

    `sections` stays an untyped map on purpose, and that is the part
    worth stating: the section *names* are chosen as the export runs
    and each one holds rows shaped by the table it came from. Typing
    those would mean a model per table, maintained in step with every
    migration, on a path where being out of step means under-
    reporting somebody's data. The envelope is a contract; the
    sections are an inventory.
    """

    subject: str
    generated_at: str
    #: Article 15 context — purposes, categories, recipients,
    #: retention, source. Built by `describe_processing`.
    processing: Any
    #: Field name to the reason it is not in `sections`. Named as
    #: withheld rather than silently absent, which is the whole
    #: difference between a redaction and an incomplete answer.
    withheld: dict[str, str]
    #: Section name to its rows. See the class note.
    sections: dict[str, Any]

    #: Section name to the sentence saying it hit its cap.
    #:
    #: Absent when nothing was capped, which is why it is optional
    #: rather than an empty dict — and why it is here at all: a cap
    #: this did not carry would be an export that silently
    #: under-reported, which is the failure the whole mechanism
    #: exists to prevent. The first version of this model omitted it
    #: and would have dropped it on the way out.
    truncated: dict[str, str] | None = None

    #: Present only when an administrator of one organisation asked.
    scope: str | None = None


class SubjectAccessService(metaclass=SingletonMeta):
    """Assemble one person's data into a report.

    No longer a `SQLAlchemySessionManager`. Inheriting it is how the
    layer boundary got erased here: a service that *is* a session
    manager can open one, and this one did — thirty hand-written
    statements and a `rows()` closure, in the module that answers a
    legal obligation. The reads live in `SubjectAccessDB` now and this
    assembles the report out of them.
    """

    def __init__(self, *, db: SubjectAccessDB | None = None) -> None:
        self._db = db or SubjectAccessDB()

    async def export(self, *, email: str, org_id: str | None = None) -> SubjectAccessReport:
        """Everything this deployment holds about the address, plus context.

        Keyed on an email address rather than a user id because the subject
        may have no account at all and still appear in sign-in attempts.
        """
        if not email or '@' not in email:
            raise ValueError('a subject access request needs an email address')

        generated_at = datetime.now(timezone.utc)
        report: dict[str, Any] = {
            'subject': email,
            'generated_at': generated_at.isoformat(),
            'processing': describe_processing(),
            'withheld': {
                'users.hashed_password': (
                    'A password hash, excluded deliberately. It is not meaningful '
                    'personal data and disclosing it would help an attacker who '
                    'persuaded an administrator to run this export.'
                ),
                'user_roles.email_verification_token': ('A single-use token, excluded for the same reason.'),
                'registered_clients.decided_by_user_id': (
                    'Which super admin approved or revoked a service registered with this '
                    'deployment. The same decision is in your own audit activity above.'
                ),
                'extension_priority.set_by_user_id': (
                    'Which super admin set the priority order of extensions, which decides '
                    'which of two has a contested part of igni. The same change is in your '
                    'own audit activity above.'
                ),
                'service_attachments.content': (
                    'The bytes of files a service sent your agent. They are described '
                    'above; the bytes were the service\'s to send, are kept at most 14 '
                    'days, and the copy imported into your chat is in your igni.'
                ),
                'vm_credentials.secret_hash': (
                    "Hashes of your cloud VM's credentials. A hash cannot be used to "
                    'sign in, but it is a credential check, not information about you.'
                ),
                'vm_credentials.previous_hashes': (
                    "Hashes of your cloud VM's earlier credentials, kept to notice one "
                    'being reused. Withheld for the same reason.'
                ),
                'producer_feed_marks.producer_id': (
                    "That something about you changed and the cloud's gateway has yet to "
                    'be told: kept for seconds, until its next poll, and saying nothing the '
                    "gateway's feed above does not."
                ),
                'platform_vm_caps.updated_by_user_id': (
                    "Which super admin set the platform's caps on VM profiles. The same "
                    'change is in your own audit activity above.'
                ),
                'org_vm_quotas.updated_by_user_id': (
                    "Which super admin set an organisation's quota of awake VM time. The "
                    'same change is in your own audit activity above.'
                ),
                'producer_exports.content_oid': (
                    "An archive of your data in an organisation you were removed from, "
                    'exported for that organisation, which controls it, during its '
                    'retention. Ask the organisation for it; it is deleted when it expires.'
                ),
                'platform_retention.updated_by_user_id': (
                    "Which super admin set the platform's longest retention of removed "
                    'members. The same change is in your own audit activity above.'
                ),
                'org_retention.updated_by_user_id': (
                    "Which admin set an organisation's retention of removed members. The "
                    'same change is in your own audit activity above.'
                ),
                'platform_space_caps.updated_by_user_id': (
                    "Which super admin set the platform's caps on apps' spaces. The same "
                    'change is in your own audit activity above.'
                ),
                'org_space_quotas.updated_by_user_id': (
                    "Which admin set an organisation's limits on apps' spaces. The same "
                    'change is in your own audit activity above.'
                ),
                'app_spaces.external_id': (
                    "An app's own name for one of its users. It never names an igni "
                    "account, so nothing of yours is in it; the app's operator answers for "
                    'its users.'
                ),
                'audit_events.detail': (
                    'Per-route detail attached to your audit records. Withheld because on '
                    'some routes it describes a third party rather than you — an erasure '
                    'you performed carries the address of the person erased, an access '
                    'review carries the member reviewed. The shape differs per route, so '
                    'there is no single rule for what to remove, and returning it would '
                    "disclose other people's data in the act of answering your request. "
                    'Ask the controller if you need a specific record explained.'
                ),
            },
            'sections': {},
        }

        db = self._db

        account = await db.account(email)
        report['sections']['account'] = account

        if not account:
            # No local account. The person may still appear in
            # authentication attempts, so the report continues rather than
            # returning empty — "we hold nothing" would be a false answer
            # to Article 15.
            report['sections']['note'] = (
                'No user account exists for this address. Any records below come '
                'from attempts to sign in with this address.'
            )
            user_id = None
        else:
            user_id = str(account[0]['user_id'])

        if user_id is not None:
            report['sections']['deployment_role'] = await db.deployment_role(user_id)
            report['sections']['organisation_memberships'] = await db.organisation_memberships(user_id)
            report['sections']['organisations_owned'] = await db.organisations_owned(user_id)

        # The organisation's own records: in its own database, and read
        # only for the organisation whose administrator is asking.
        if user_id is not None and org_id is not None:
            report['sections']['group_memberships'] = await db.group_memberships(org_id, user_id)

            report['sections']['model_usage'] = await db.model_usage(org_id, user_id)
            _note_if_capped(report, 'model_usage', report['sections']['model_usage'], _ACTIVITY_LIMIT)

            report['sections']['own_activity'] = await db.own_activity(org_id, user_id)
            _note_if_capped(report, 'own_activity', report['sections']['own_activity'], _ACTIVITY_LIMIT)

            report['sections']['things_they_created'] = await db.things_they_created(
                org_id=org_id, user_id=user_id, email=email
            )

        if user_id is not None and org_id is not None:
            # The registered services' own records about this person in
            # this organisation, each under the service's name.
            answers = await ServiceSubjects().export(user_id, org_id=org_id)
            if answers.sections:
                report['sections']['services'] = answers.sections
            for name in answers.missing:
                report.setdefault('truncated', {})[name] = (
                    f'{name} did not answer; what it holds about this person is missing from this '
                    'export. Ask again, or ask an operator to check it.'
                )

        # Keyed on the address, which catches the authentication
        # attempts made before any account existed.
        report['sections']['authentication_attempts'] = await db.authentication_attempts(org_id, email)
        _note_if_capped(
            report,
            'authentication_attempts',
            report['sections']['authentication_attempts'],
            _ACTIVITY_LIMIT,
        )

        if org_id is not None:
            report['scope'] = (
                f'Prepared by an administrator of organisation {org_id}, from its records. '
                'Sections that are not organisation-specific — the account and sign-in '
                'attempts — describe the person across this deployment. Another '
                "organisation's records about the same address are not included: they are "
                "that organisation's to disclose."
            )
        else:
            report['scope'] = (
                'Prepared without an organisation, so only records that are not '
                "organisation-specific are included. Each organisation's own records — groups, "
                'usage, activity — are not.'
            )

        return SubjectAccessReport.model_validate(report)
