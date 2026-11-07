/**
 * One organisation, as every dashboard read sees it: busy enough that
 * each card has something to show and each "needs attention" rule has
 * something to say.
 */
import type {
  ApiKey,
  AuditEventPage,
  ErasureList,
  InferenceStatus,
  NodeStatus,
  OrgAppList,
  OrgGroupList,
  TeamMemberList,
  UserMe,
  VmCommandRecord,
  VmCredential,
  VmProfilesOverview,
} from '../../../types'

export const NOW = new Date('2026-06-01T12:00:00Z')

export const ADMIN: UserMe = {
  user_id: 'u1',
  email: 'alex@acme.example',
  full_name: 'Alex Chen',
  created_at: '2026-01-01T00:00:00Z',
  role: 'admin',
  org_id: 'org-1',
  org_display_name: 'Acme',
  organizations: [{ id: 'org-1', slug: 'acme', display_name: 'Acme', role: 'admin' }],
}

export const MEMBER: UserMe = { ...ADMIN, user_id: 'u2', email: 'bo@acme.example', full_name: 'Bo Diaz', role: 'member' }

export const USAGE = {
  data: [{ date: '2026-05-31', input_tokens: 1000, output_tokens: 500, total_tokens: 1500, request_count: 7 }],
  total_tokens: 1_500_000,
  total_input_tokens: 1_000_000,
  total_output_tokens: 500_000,
  total_requests: 42,
}

const member = (over: Partial<TeamMemberList['members'][number]>): TeamMemberList['members'][number] => ({
  id: 'u1',
  user_id: 'u1',
  email: 'alex@acme.example',
  full_name: 'Alex Chen',
  role: 'admin',
  status: 'active',
  total_requests: 10,
  total_tokens: 900_000,
  last_active: '2026-05-31T09:00:00Z',
  joined_at: '2026-01-01T00:00:00Z',
  ...over,
})

export const TEAM: TeamMemberList = {
  members: [
    member({}),
    member({ id: 'u2', user_id: 'u2', email: 'bo@acme.example', full_name: 'Bo Diaz', role: 'member', total_tokens: 600_000 }),
    member({ id: 'u3', user_id: 'u3', email: 'cy@acme.example', full_name: null, role: 'member', status: 'invited', total_tokens: 0, last_active: null }),
  ],
  total: 3,
}

export const GROUPS: OrgGroupList = {
  groups: [
    {
      id: 'g1',
      org_id: 'org-1',
      name: 'Everyone',
      description: null,
      default_model: null,
      is_default: true,
      member_count: 3,
      entry_count: 4,
    } as OrgGroupList['groups'][number],
    {
      id: 'g2',
      org_id: 'org-1',
      name: 'Platform',
      description: null,
      default_model: null,
      is_default: false,
      member_count: 1,
      entry_count: 2,
    } as OrgGroupList['groups'][number],
  ],
  total: 2,
}

const node = (over: Partial<NodeStatus>): NodeStatus => ({
  id: 'gpu-a',
  base_url: 'http://gpu-a:8000',
  models: ['igni-1.0'],
  pools: ['chat'],
  multimodal: false,
  source: 'registered',
  in_flight: 2,
  max_concurrent: 8,
  utilization: 0.25,
  health: { state: 'healthy', detail: null, latency_ms: 12 },
  metrics: null,
  last_seen_seconds_ago: 3,
  registered_at: 1,
  drain: null,
  ...over,
})

export const INFERENCE: InferenceStatus = {
  pools: [
    {
      pool: 'chat',
      nodes: [node({}), node({ id: 'gpu-b', health: { state: 'unreachable', detail: 'No response', latency_ms: null } })],
      total_in_flight: 2,
      total_capacity: 16,
      utilization: 0.125,
    },
  ],
  registration_enabled: true,
  heartbeat_interval_seconds: 10,
}

export const APPS: OrgAppList = {
  apps: [
    {
      app_id: 'a1',
      client_id: 'app-review-bot',
      name: 'Review bot',
      owner_user_id: 'u1',
      owner_email: 'alex@acme.example',
      thumbprint: 'x',
      vm_profile_id: null,
      created_at: '2026-05-20T00:00:00Z',
    },
  ],
}

export const VM_PROFILES = {
  org_id: 'org-1',
  caps: { max_memory_mib: 8192, max_vcpus: 4, max_idle_before_pause_s: null },
  quota: { org_id: 'org-1', awake_vm_seconds_per_month: 360_000, enforced: true, updated_at: null },
  default_profile_id: 'p1',
  profiles: [
    { id: 'p1', name: 'Standard', is_default: true, within_caps: true, memory_mib: 4096 },
    { id: 'p2', name: 'Huge', is_default: false, within_caps: false, memory_mib: 16384 },
  ],
  groups: [{ id: 'g1', name: 'Everyone', vm_profile_id: null }],
  apps: [{ id: 'a1', name: 'Review bot', vm_profile_id: null }],
} as unknown as VmProfilesOverview

export const ERASURES: ErasureList = {
  org_id: 'org-1',
  erasures: [
    {
      producer: { kind: 'person', id: 'old' },
      name: 'Old member',
      incarnation: 'i1',
      removed_at: '2026-05-01T00:00:00Z',
      erase_at: '2026-06-04T00:00:00Z',
      state: 'scheduled',
      erased_at: null,
      legal_hold: false,
      exports: 0,
      discarded_by_user_id: null,
      discarded_at: null,
    },
  ],
}

export const AUDIT: AuditEventPage = {
  events: [
    {
      id: 'e1',
      occurred_at: '2026-06-01T11:00:00Z',
      actor_email: 'bo@acme.example',
      actor_user_id: 'u2',
      method: 'DELETE',
      route: '/v1/portal/groups/g1',
      target_type: 'group',
      target_id: 'g1',
      status_code: 403,
      outcome: 'denied',
      client_host: null,
      detail: null,
    },
  ],
  next_before: null,
}

export const API_KEYS: ApiKey[] = [
  { key_id: 'k1', name: 'ci-bot', user_id: 'u1', created_at: '2026-03-01T00:00:00Z', expires_at: '2026-06-04T00:00:00Z', last_used_at: null, expired: false },
  { key_id: 'k2', name: 'laptop', user_id: 'u1', created_at: '2026-01-01T00:00:00Z', expires_at: '2026-12-01T00:00:00Z', last_used_at: null, expired: false },
]

export const VM_CREDENTIALS: VmCredential[] = [
  {
    credential_id: 'c1',
    org_id: 'org-1',
    producer: { kind: 'person', id: 'u1' },
    created_at: '2026-05-31T00:00:00Z',
    last_used_at: null,
    idle_expires_at: '2026-06-30T00:00:00Z',
    awaiting_pickup: true,
    picked_up_at: null,
    generation: 1,
  },
]

const command = (over: Partial<VmCommandRecord>): VmCommandRecord => ({
  command_id: 'cmd1',
  org_id: 'org-1',
  producer: { kind: 'person', id: 'u1' },
  command: 'apt-get install -y ripgrep',
  timeout_s: 300,
  job_id: null,
  wake: true,
  requested_by: { kind: 'person', id: 'u1', acting_as: 'org_admin' },
  state: 'finished',
  handed_to: null,
  created_at: '2026-06-01T10:00:00Z',
  handed_out_at: null,
  started_at: null,
  finished_at: '2026-06-01T10:01:00Z',
  exit_code: 0,
  output: null,
  error: null,
  ...over,
})

export const COMMANDS: VmCommandRecord[] = [
  command({}),
  command({ command_id: 'cmd2', command: 'npm run build', exit_code: 1 }),
  command({ command_id: 'cmd3', command: 'sleep 60', state: 'running', finished_at: null, exit_code: null }),
]
