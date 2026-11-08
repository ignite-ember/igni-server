// ─── Usage ──────────────────────────────────────────────────────────

export interface UsageDataPoint {
  date: string
  input_tokens: number
  output_tokens: number
  total_tokens: number
  request_count: number
}

// ─── User ───────────────────────────────────────────────────────────

// ─── Inference nodes ────────────────────────────────────────────────

/**
 * Where a node came from. The two are operated differently — a
 * configured node changes by editing config and redeploying, a
 * registered one by whatever started the cluster — so the portal shows
 * which, to tell an operator where to go looking.
 */
export type NodeSource = 'static' | 'registered' | 'pinned'

/**
 * Whether a node is answering.
 *
 * ``unknown`` means we have not managed to probe it yet. It is rendered
 * the same amber as ``degraded`` rather than getting a fourth colour:
 * somebody asking "is my cluster up?" is not helped by grey.
 */
export type HealthState = 'healthy' | 'degraded' | 'unreachable' | 'unknown'

export interface NodeHealth {
  state: HealthState
  /** Written for a person: "No response within 3s", not a status code. */
  detail: string | null
  latency_ms: number | null
}

/**
 * The node's own gauges, from its `/metrics` endpoint.
 *
 * `running` differs from `in_flight` whenever igni is not the GPU's only
 * client — someone calling it directly, or another service sharing it —
 * and where they disagree the node is right. Every field is optional
 * because not every node is vLLM; absent means "could not ask", never
 * "zero".
 */
export interface NodeMetrics {
  running: number | null
  waiting: number | null
  kv_cache_percent: number | null
  /** hits / queries over the node's lifetime — whether sticky routing pays off. */
  prefix_cache_hit_percent: number | null
  /** Sequences discarded and redone for want of KV blocks. */
  preemptions: number | null
}

export interface NodeStatus {
  id: string
  base_url: string
  /** Every id this node answers to: the base model, plus any LoRA adapters. */
  models: string[]
  multimodal: boolean
  source: NodeSource
  in_flight: number
  max_concurrent: number
  utilization: number
  health: NodeHealth
  metrics: NodeMetrics | null
  /** Registered nodes only; a configured node has no heartbeat to be late on. */
  last_seen_seconds_ago: number | null
  registered_at: number | null
  /**
   * Set when an operator took this node out of rotation. The node is
   * still listed — a switch whose subject disappears cannot be switched
   * back — so this is what tells the two states apart.
   */
  drain: NodeDrain | null
}

export interface NodeDrain {
  by: string
  by_email: string | null
  at: string
  reason: string | null
}


/** A node an admin added by hand. The api_key is never returned. */
export interface PinnedNode {
  node_id: string
  base_url: string
  models: string[]
  max_concurrent: number
  multimodal: boolean
  enabled: boolean
  has_api_key: boolean
}

export interface PinnedNodeInput {
  node_id: string
  base_url: string
  models: string[]
  api_key?: string
  max_concurrent: number
  multimodal: boolean
}

/** An address to ask what it serves, before adding it as a node. */
export interface NodeProbeRequest {
  base_url: string
  api_key?: string
}

/**
 * What an address turned out to be, when asked directly.
 *
 * ``reachable: false`` arrives as a 200 with a reason, not an error:
 * "I could not reach that" is a successful answer to "what is here?".
 */
export interface NodeProbeResult {
  reachable: boolean
  models: string[]
  detail: string | null
  latency_ms: number | null
  /**
   * It answered, but refused without a token — the likely outcome of
   * pasting a vLLM node started with `--api-key`, which protects `/v1`.
   */
  needs_credentials: boolean
}

export interface InferenceStatus {
  nodes: NodeStatus[]
  total_in_flight: number
  total_capacity: number
  utilization: number
  /**
   * Whether this deployment accepts self-registration at all. Without
   * it, "nothing has registered" and "registration is switched off" look
   * identical, and they call for completely different fixes.
   */
  registration_enabled: boolean
  heartbeat_interval_seconds: number
}

/** GET /portal/inference/models: every model id the nodes serve, for choosing one. */
export interface AvailableModels {
  models: string[]
}

export interface OrgSummary {
  id: string
  slug: string
  display_name: string
  role: 'admin' | 'member'
}

/** POST /portal/organizations. */
export interface CreateOrgRequest {
  display_name: string
  domains: string[]
}

/** POST and PATCH /portal/organizations: the organisation as it now is. */
export interface OrgChanged {
  id: string
  slug: string
  display_name: string
}

/** DELETE /portal/organizations/{id}. */
export interface DeleteOrgResponse {
  message: string
}

/** DELETE /portal/me. */
export interface DeleteAccountResponse {
  message: string
}

export interface UserMe {
  user_id: string
  email: string | null
  full_name: string | null
  created_at: string | null
  role?: 'admin' | 'member'
  pending_email?: string | null
  auth_provider?: 'email' | 'google' | 'oidc' | 'scim'
  org_id?: string | null
  org_slug?: string | null
  org_display_name?: string | null
  organizations?: OrgSummary[]
  /** Owns the install: creates organisations, makes other super admins. */
  is_super_admin?: boolean
}

/** POST /v1/cli/token. The page forwards only `handoff_code`; the CLI redeems it. */
export interface CLITokenResponse {
  access_token: string
  handoff_code: string
}

// ─── Super admins ───────────────────────────────────────────────────

/** An organisation as a super admin sees it: what it is and what it owns. */
export interface DeploymentOrg {
  id: string
  slug: string
  display_name: string
  domains: string[]
  members: number
}

export interface SuperAdmin {
  user_id: string
  email: string | null
  full_name: string | null
}

// ─── Registered clients ─────────────────────────────────────────────

export type RegisteredClientKind = 'service' | 'app'
export type RegisteredClientState = 'pending' | 'approved' | 'revoked'

/**
 * A service or app that registered itself with this install.
 *
 * `thumbprint` identifies the key it registered with. The service
 * prints the same value in its own log, and a super admin compares the
 * two before approving: a match means the registration came from the
 * service they deployed and not from somebody else on the network.
 */
export interface RegisteredClient {
  client_id: string
  kind: RegisteredClientKind
  name: string
  base_url: string
  redirect_uris: string[]
  thumbprint: string
  state: RegisteredClientState
  created_at: string
  decided_at: string | null
  /** What it ships to people's machines; `null` until it uploads one. */
  extension: ServiceExtensionSummary | null
  /** A newer version that asks for more, waiting for a super admin. */
  held_version?: string | null
}

/** The extension a service ships, as the list of services shows it. */
export interface ServiceExtensionSummary {
  extension_id: string
  version: string
  name: string
  description: string
  published_at: string
}

/** One of the plugin's own agents, under the extension's namespace. */
export interface ExtensionPluginAgent {
  file: string
  name: string
  description: string
}

/** An agent the extension overrides, as its own file defines it while it runs. */
export interface ExtensionAgentOverride {
  agent: string
  file: string
  description: string
  exclusive: boolean
  when: string | null
}

export interface ExtensionPluginSkill {
  name: string
  description: string
}

export interface ExtensionPlugin {
  agents?: ExtensionPluginAgent[]
  agent_overrides?: ExtensionAgentOverride[]
  skills?: ExtensionPluginSkill[]
}

/** A whole prompt an extension brings: `main`, `plan_mode`, `agent:<name>`. */
export interface ExtensionImpactOverride {
  target: string
  /** Its sections; an agent's `frontmatter` is implied. */
  sections: string[]
  exclusive: boolean
  when: string | null
}

/** One change an extension makes to a prompt. */
export interface ExtensionImpactEdit {
  target: string
  op: 'append' | 'insert' | 'replace'
  /** The section replaced, or inserted against. */
  section: string | null
  position: 'before' | 'after' | null
  /** The new section's name, for an insert or an append. */
  name: string | null
  when: string | null
}

/** What an extension changes in igni, read off its manifest by igni server. */
export interface ExtensionImpact {
  extension_id: string
  name: string
  version: string
  prompt_overrides: ExtensionImpactOverride[]
  prompt_edits: ExtensionImpactEdit[]
  anchors: string[]
  commands: string[]
}

/** An edit that would land nowhere, and whose override leaves it out. */
export interface ExtensionDropped {
  claim: string
  reason: string
  by: string
}

/** Two or more extensions claiming one thing, and who has it by priority. */
export interface ExtensionCollision {
  claim: string
  extensions: string[]
  /**
   * The highest in the priority order. `null` only for an exclusive
   * override a higher extension's edits switched off, with no other
   * override left — then nobody holds the whole prompt.
   */
  winner: string | null
  /** The extensions that lost something in it. */
  losers: string[]
}

/** One thing an extension does not get, and why — `lower priority than <extension>`. */
export interface ExtensionSwitchedOff {
  claim: string
  reason: string
}

export interface ExtensionReview {
  impact: ExtensionImpact
  unknown_targets: string[]
  igni_commands: string[]
  collisions: ExtensionCollision[]
  /** What it does not get, and why. */
  switched_off: ExtensionSwitchedOff[]
  /** Its edits another extension's override leaves out. */
  dropped: ExtensionDropped[]
  /** Other extensions' edits its own overrides leave out, by extension. */
  drops_others: Record<string, ExtensionDropped[]>
}

/** One extension in the priority order. */
export interface ExtensionPriorityEntry {
  extension_id: string
  name: string
  service: string
}

/**
 * The super admin's priority order of extensions, highest first,
 * deployment-wide. The higher one wins every collision; prompt edits
 * stack and extensions' hooks are asked in it.
 */
export interface ExtensionPriority {
  entries: ExtensionPriorityEntry[]
  /** Who set it; empty while it is the order services were approved in. */
  set_by: string
  set_at: string | null
}

/**
 * Everything the extension would put on people's machines.
 *
 * `manifest` is the whole manifest as the service sent it, so it is read
 * defensively (see `lib/extensionManifest`): any key may be missing.
 */
export interface ServiceExtensionDetail extends ServiceExtensionSummary {
  sha256: string
  manifest: Record<string, unknown>
  plugin: ExtensionPlugin
  /** Its impact on igni and every collision it is in. */
  review?: ExtensionReview | null
}

/** One registration, with its extension in full. */
export interface RegisteredClientDetail extends RegisteredClient {
  extension_detail: ServiceExtensionDetail | null
  /** The held version and what it adds, if one is waiting. */
  held_update?: HeldExtensionUpdate | null
}

/** One thing a held version asks for that the approved version did not. */
export interface AddedGrant {
  kind: string
  /** The kind in words: "Network egress", "Secret", "Prompt section". */
  label: string
  /** What is asked for: a host, a path, a tool's name, `main#rules`. */
  item: string
  /** What the approved version had, where this widens something it had. */
  was: string | null
}

/**
 * An approved service's upload that asks for more than the approved
 * version. Machines keep receiving `approved_version` until a super
 * admin approves this one; rejecting it discards it.
 */
export interface HeldExtensionUpdate {
  client_id: string
  service: string
  extension_id: string
  name: string
  approved_version: string | null
  version: string
  sha256: string
  uploaded_at: string
  comparison: { wider: boolean; added: AddedGrant[] }
}

/** What a registered client is asking a person to sign in to. */
export interface AuthorizationAsk {
  client_name: string
  organisations: { org_id: string; name: string }[]
}

// ─── Team ───────────────────────────────────────────────────────────

export type TeamRole = 'admin' | 'member'
export type TeamMemberStatus = 'active' | 'invited' | 'deactivated'

export interface TeamMember {
  id: string
  user_id: string
  email: string
  full_name: string | null
  role: TeamRole
  status: TeamMemberStatus
  total_requests: number
  total_tokens: number
  last_active: string | null
  joined_at: string
}

/**
 * The answer to POST /portal/team/invite.
 *
 * ``invite_url`` is always populated, not only when email is off: it is
 * not a secret — the invite is claimed by signing up with the invited
 * address, so the link is derivable from the address alone — and an
 * admin who would rather send it over Slack should not have to
 * reconstruct it.
 *
 * ``emailed`` is false when this deployment has no SMTP relay, which is
 * a supported configuration. Passing the link on is then the admin's
 * job, and the UI has to say so rather than claiming a message was
 * sent.
 */
export interface InvitedMember extends TeamMember {
  invite_url: string
  emailed: boolean
}

export interface TeamMemberList {
  members: TeamMember[]
  total: number
}

// ─── Groups ─────────────────────────────────────────────────────────

/** What a group can hold. Mirrors app/services/portal/entry_kinds.py; lib/entryKinds.ts describes each. */
export type EntryKind =
  | 'agents'
  | 'skills'
  | 'commands'
  | 'workflows'
  | 'rules'
  | 'output-styles'
  | 'hooks'
  | 'tools'
  | 'mcps'
  | 'plugins'
  | 'scripts'
  | 'settings'

/**
 * The format an entry's content is in. Not a choice: igni's loaders honour
 * exactly one per kind, and anything else is dropped rather than rejected.
 */
export type ContentType = 'markdown' | 'json' | 'yaml' | 'javascript' | 'python' | 'shell'

/**
 * Brand overrides a group's people see in the igni client.
 *
 * Every field is optional and every unset one falls back to what igni
 * ships, so a theme that sets only `accent` leaves the rest alone. The
 * server validates all of it: colours must be hex, and `brand_mark`
 * must be a base64 `data:` URI — not a URL, because the desktop
 * client's CSP only allows images from `data:`.
 */
export interface GroupTheme {
  accent?: string | null
  danger?: string | null
  success?: string | null
  brand_name?: string | null
  brand_mark?: string | null
}

export interface OrgGroup {
  id: string
  org_id: string
  name: string
  description: string | null
  /**
   * Which model this group's people get when they do not name one —
   * usually a LoRA adapter. Null means the deployment default, whatever
   * the chosen node advertises first.
   */
  default_model: string | null
  /**
   * Brand overrides for this group's people. Null is igni's palette,
   * which is every group that has not set one.
   */
  theme: GroupTheme | null
  /**
   * The group seeded with what igni ships. One per org, and the one
   * people usually mean when they copy a group.
   */
  is_default: boolean
  created_by_user_id: string
  created_at: string
  updated_at: string
}

export interface OrgGroupDetail extends OrgGroup {
  member_count: number
  entry_count: number
}

export interface OrgGroupList {
  // The list endpoint counts members and entries for each group, and
  // both are shown — on the row, and when picking a group to copy.
  groups: OrgGroupDetail[]
  total: number
  offset?: number
  limit?: number
}

export interface OrgGroupMember {
  id: string
  group_id: string
  user_id: string
  added_by_user_id: string
  added_at: string
  email: string | null
  full_name: string | null
}

export interface OrgGroupMemberList {
  members: OrgGroupMember[]
  total: number
}

export interface OrgGroupEntry {
  id: string
  group_id: string
  kind: EntryKind
  entry_name: string
  content: string
  content_type: ContentType
  enabled: boolean
  created_by_user_id: string
  created_at: string
  updated_at: string
  source_url: string | null
  source_ref: string | null
  source_subdir: string | null
  /**
   * Agents only: which model this one runs against. Null falls back to
   * the group's default, then the deployment's.
   */
  model: string | null
}

/**
 * POST /portal/groups/{id}/entries.
 *
 * The server tells an absent key from a null one, so the source fields
 * ride along only for plugins and `model` only for an agent that names one.
 */
export interface CreateOrgGroupEntryRequest {
  kind: EntryKind
  entry_name: string
  content: string
  content_type: ContentType
  enabled: boolean
  source_url?: string | null
  source_ref?: string | null
  source_subdir?: string | null
  model?: string
}

export interface OrgGroupEntryList {
  entries: OrgGroupEntry[]
  total: number
  /**
   * After a create or update: anything worth saying about what was just
   * written, such as a mistyped tool name. The write still succeeded.
   */
  warnings?: string[]
}

/** One approved extension, and whether this group's people get it (off by default). */
export interface GroupExtension {
  extension_id: string
  name: string
  description: string
  service_url: string
  enabled: boolean
}

export interface GroupExtensionList {
  extensions: GroupExtension[]
}

export interface OrgGroupSummary {
  id: string
  org_id: string
  name: string
}

/** The one group a person of the organisation is in. Everybody is in exactly one. */
export interface OrgGroupMembership {
  user_id: string
  group_id: string
  group_name: string
  is_default: boolean
}

export interface OrgGroupMembershipList {
  memberships: OrgGroupMembership[]
}

// ─── The cloud: apps, VM profiles, commands ─────────────────────────

/** An app: the organisation's other kind of producer, acting only as itself. */
export interface OrgApp {
  app_id: string
  client_id: string
  name: string
  owner_user_id: string | null
  owner_email: string | null
  thumbprint: string
  /** Null: the organisation's default VM profile. */
  vm_profile_id: string | null
  created_at: string
}

export interface OrgAppList {
  apps: OrgApp[]
}

export interface VmPackages {
  apt: string[]
  pip: string[]
  npm: string[]
}

/** Everything a VM profile says, as an org admin writes it. */
export interface VmProfileSettings {
  name: string
  /** The most memory the VM may have: its size at boot. */
  memory_mib: number
  /** What it normally has; the node gives more, up to `memory_mib`, while it needs it. */
  normal_memory_mib: number
  /** What the node expects the VM to use, for placing it; null: the node's rule. */
  expected_memory_mib: number | null
  vcpus: number
  /** Null: never paused. */
  idle_before_pause_s: number | null
  packages: VmPackages
  setup_script: string | null
  /**
   * Apps and their spaces only: an app's VM not woken for this many days
   * has its memory, snapshots, volume and keys erased. Null: never.
   */
  erase_after_idle_days?: number | null
  /**
   * Whether a VM with the profile may reach the internet. False: only what
   * its node allows. A security grant; a node that may not grant it
   * (air-gapped, or without the capability) keeps the VM internal-only.
   */
  internet?: boolean
}

export interface VmProfile extends VmProfileSettings {
  id: string
  is_default: boolean
  version: string
  packages_version: string
  within_caps: boolean
  created_at: string
  updated_at: string
}

export interface PlatformVmCaps {
  max_memory_mib: number
  max_vcpus: number
  max_idle_before_pause_s: number | null
  /** The largest volume a profile may let a VM grow to, in GiB. */
  max_volume_gib?: number
  /** The longest `erase_after_idle_days` a profile may have; null: no maximum. */
  max_erase_after_idle_days?: number | null
  updated_at?: string | null
}

export interface OrgVmQuota {
  org_id: string
  awake_vm_seconds_per_month: number | null
  enforced: boolean
  updated_at: string | null
}

export interface ProducerAssignment {
  id: string
  name: string
  vm_profile_id: string | null
}

export interface VmProfilesOverview {
  org_id: string
  caps: PlatformVmCaps
  quota: OrgVmQuota
  default_profile_id: string
  profiles: VmProfile[]
  groups: ProducerAssignment[]
  apps: ProducerAssignment[]
}

/** What an app or an extension asked its VM to get: everything a profile says but its name. */
export type VmProfileRequested = Omit<VmProfileSettings, 'name'> & {
  volume_gib?: number
  volume_max_gib?: number
}

/** One request, pending until an org admin approves or rejects it (service-identity §12.3). */
export interface VmProfileRequest {
  id: string
  org_id: string
  subject: { kind: 'app' | 'extension'; id: string; name: string }
  state: 'pending' | 'approved' | 'rejected'
  /** Exactly what would run. */
  requested: VmProfileRequested
  /** What an approval names: it covers exactly this version. */
  version: string
  packages_version: string
  within_caps: boolean
  requested_by: { kind: 'app' | 'service'; id: string }
  requested_at: string
  decided_by: { kind: string; id: string; acting_as: string } | null
  decided_at: string | null
  reason: string | null
  /** The profile an approval made. */
  profile_id: string | null
}

export type ProducerKind = 'person' | 'app'

export interface ProducerRef {
  kind: ProducerKind
  id: string
}

export interface VmRequester {
  kind: string
  id: string
  acting_as: 'producer' | 'org_admin' | 'super_admin'
}

/** One command in one VM — and its audit record. */
export interface VmCommandRecord {
  command_id: string
  org_id: string
  producer: ProducerRef
  command: string
  timeout_s: number
  job_id: string | null
  wake: boolean
  requested_by: VmRequester
  state: 'queued' | 'handed_out' | 'running' | 'finished' | 'cancelled'
  handed_to: string | null
  created_at: string
  handed_out_at: string | null
  started_at: string | null
  finished_at: string | null
  exit_code: number | null
  output: string | null
  error: string | null
}

export type JobScopeKind = 'group' | 'profile' | 'org' | 'platform'

export interface VmJobRecord {
  job_id: string
  org_id: string
  scope: { kind: JobScopeKind; id: string | null }
  command: string
  timeout_s: number
  requested_by: VmRequester
  created_at: string
  cancelled_at: string | null
  handed_out: number
  finished: number
}

/** A producer's VM credential, as the portal shows it — never the secret. */
export interface VmCredential {
  credential_id: string
  org_id: string
  producer: ProducerRef
  created_at: string
  last_used_at: string | null
  idle_expires_at: string
  awaiting_pickup: boolean
  picked_up_at: string | null
  generation: number
}

/** How long a personal API key lives, chosen when it is made. */
export type ApiKeyLifetime = 30 | 90 | 365

/** A personal API key as the portal lists it: never the key itself. */
export interface ApiKey {
  key_id: string
  name: string
  user_id: string
  created_at: string
  expires_at: string
  last_used_at: string | null
  expired: boolean
}

/** A key just made — the one time the key itself is shown. */
export interface CreatedApiKey extends ApiKey {
  api_key: string
}

/** A legal hold on an app: its VM (and its spaces') is not erased while it stands. */
export interface AppHold {
  org_id: string
  app_id: string
  reason: string
  placed_by_user_id: string | null
  placed_at: string
}

/** One removed incarnation of a held person, and when it is erased (null while held). */
export interface HeldIncarnation {
  incarnation: string
  removed_at: string | null
  erase_at: string | null
}

/** A legal hold on a person in an organisation. */
export interface PersonHold {
  org_id: string
  user_id: string
  reason: string
  placed_by_user_id: string
  placed_at: string
  /** Their removed incarnations in this organisation not yet erased. */
  incarnations: HeldIncarnation[]
}

/** An organisation's legal holds: on people, and on apps. */
export interface LegalHoldList {
  holds: PersonHold[]
  apps: AppHold[]
}

export type ErasureState = 'scheduled' | 'held' | 'erasing' | 'erased'

/** A removed producer's VM on its way to erasure: a former member, a discarded app or space. */
export interface Erasure {
  producer: { kind: 'person' | 'app' | 'space'; id: string }
  name: string | null
  incarnation: string
  removed_at: string | null
  erase_at: string | null
  state: ErasureState
  erased_at: string | null
  legal_hold: boolean
  /** Exports of this incarnation's data pending or ready. */
  exports: number
  discarded_by_user_id: string | null
  discarded_at: string | null
}

export interface ErasureList {
  org_id: string
  erasures: Erasure[]
}

/** An app discarded: gone at once, its VM erased in the background. */
export interface DiscardedApp {
  /** Where its erasure stands; null for an app whose VM never existed. */
  discarded: Erasure | null
  /** The new app, when a fresh start was asked for. */
  fresh_app: OrgApp | null
}

// ─── Compliance ─────────────────────────────────────────────────────

/** How the server classified an audited request: `denied` is a refusal, `error` a failure. */
export type AuditOutcome = 'success' | 'denied' | 'error'

/** One privileged action, as GET /portal/audit-events returns it. */
export interface AuditEvent {
  id: string
  occurred_at: string
  actor_email: string | null
  actor_user_id: string | null
  method: string
  route: string
  target_type: string | null
  target_id: string | null
  status_code: number
  outcome: AuditOutcome
  client_host: string | null
  detail: Record<string, unknown> | null
}

/** A page of audit events, newest first; `next_before` is the cursor for the next one. */
export interface AuditEventPage {
  events: AuditEvent[]
  next_before: string | null
}

/** Why an access review row is worth a second look. */
export type AccessFlag = 'invitation_pending' | 'never_signed_in' | 'dormant' | 'suspended'

/** One person's access to the organisation. */
export interface AccessReviewRow {
  user_id: string | null
  email: string | null
  full_name: string | null
  auth_provider: string | null
  role: TeamRole
  status: TeamMemberStatus
  granted_at: string | null
  granted_by: string | null
  last_sign_in_at: string | null
  days_since_sign_in: number | null
  flags: AccessFlag[]
}

/** The access review report, plus the counts an auditor writes down. */
export interface AccessReview {
  generated_at: string
  dormant_after_days: number
  total: number
  active: number
  needs_review: number
  rows: AccessReviewRow[]
}

/**
 * The subject access report, kept as the whole JSON document.
 *
 * It is a legal artefact shown and downloaded verbatim, so the portal
 * deliberately does not model its parts.
 */
export type SubjectAccessReport = Record<string, unknown>

/** What POST /portal/erasure redacted. */
export interface ErasureResult {
  email: string
  events_redacted: number
  logins_included: number
}
