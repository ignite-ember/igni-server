"""The columns of personal-data tables that say nothing about any person."""

from __future__ import annotations

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
        # decided — is classified in `personal_columns.py`.
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
        # Software; who set it is in `personal_columns.py`.
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
        'vm_profile_requests.id',
        'vm_profile_requests.profile_id',
        'vm_commands.id',
        'vm_commands.job_id',
        'vm_jobs.id',
        'person_mcp_approvals.id',
        # A notification's own id, and the registered service that
        # posted it. Each names a *record* or a piece of software — the
        # people involved are named by ``user_id`` and the actor
        # columns, which are classified in `personal_columns.py`.
        'notifications.id',
        'notifications.source_client_id',
        # What happened, named by the service so it cannot be told
        # twice — it identifies an event, not a person.
        'notifications.event_key',
        'inference_nodes.id',
        'audit_events.id',
        'chat_usage.id',
        'inference_nodes.node_id',
        'org_group_entries.group_id',
    }
    | {
        # Which organisation a record belongs to, where the record is not
        # about a person. `org_memberships.org_id` is personal and is in
        # `PERSONAL_DATA_COLUMNS`; an inference node's is not.
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
        'inference_nodes.base_url',
        'inference_nodes.models',
        'inference_nodes.max_concurrent',
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
        # and plumbing; who decided or asked is classified in `personal_columns.py`.
        'org_groups.vm_profile_id',
        'org_apps.vm_profile_id',
        'registered_clients.org_id',
        'vm_profiles.is_default',
        'vm_profiles.memory_mib',
        'vm_profiles.normal_memory_mib',
        'vm_profiles.vcpus',
        'vm_profiles.idle_before_pause_s',
        'vm_profiles.expected_memory_mib',
        'vm_profiles.erase_after_idle_days',
        'vm_profiles.internet',
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
        'app_legal_holds.org_id',
        'app_legal_holds.app_id',
        'producer_feed.discarded_label',
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
        # What an app or an extension asked for, and when: configuration,
        # asked for by a client, never a person.
        'vm_profile_requests.settings',
        'vm_profile_requests.version',
        'vm_profile_requests.requested_by_kind',
        'vm_profile_requests.requested_by_id',
        'vm_profile_requests.requested_at',
        'vm_profile_requests.decided_as',
        'platform_vm_caps.max_volume_gib',
        'platform_vm_caps.max_erase_after_idle_days',
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
        'api_keys.id',
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
