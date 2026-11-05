/** A VM profiles overview as the server answers it, for the cloud pages' tests. */
import type { VmProfile, VmProfileRequest, VmProfilesOverview } from '../../types'

export const STANDARD: VmProfile = {
  id: 'p-default',
  name: 'Standard',
  is_default: true,
  memory_mib: 4096,
  normal_memory_mib: 1024,
  vcpus: 1,
  idle_before_pause_s: 5,
  expected_memory_mib: null,
  packages: { apt: [], pip: [], npm: [] },
  setup_script: null,
  erase_after_idle_days: null,
  internet: false,
  version: 'v1',
  packages_version: 'pv1',
  within_caps: true,
  created_at: '2026-10-26T10:00:00Z',
  updated_at: '2026-10-26T10:00:00Z',
}

export const BROWSER: VmProfile = {
  ...STANDARD,
  id: 'p-browser',
  name: 'Browser',
  is_default: false,
  memory_mib: 8192,
  vcpus: 2,
  idle_before_pause_s: null,
  packages: { apt: ['chromium'], pip: [], npm: [] },
  erase_after_idle_days: 30,
  within_caps: false,
}

export const OVERVIEW: VmProfilesOverview = {
  org_id: 'demo-org-001',
  caps: { max_memory_mib: 8192, max_vcpus: 4, max_idle_before_pause_s: 3600, max_erase_after_idle_days: null },
  quota: { org_id: 'demo-org-001', awake_vm_seconds_per_month: null, enforced: false, updated_at: null },
  default_profile_id: 'p-default',
  profiles: [STANDARD, BROWSER],
  groups: [{ id: 'g-legal', name: 'Legal', vm_profile_id: null }],
  apps: [],
}

/** An app asking for a headless browser: Chromium placed by the setup script, Playwright's MCP server from npm. */
export const BROWSER_REQUEST: VmProfileRequest = {
  id: 'r-bot',
  org_id: 'demo-org-001',
  subject: { kind: 'app', id: 'app-bot', name: 'Review bot' },
  state: 'pending',
  requested: {
    memory_mib: 4096,
    normal_memory_mib: 2048,
    vcpus: 2,
    idle_before_pause_s: 10,
    expected_memory_mib: null,
    packages: { apt: ['fonts-liberation', 'unzip'], pip: [], npm: ['@playwright/mcp@0.0.84'] },
    setup_script: 'ln -sf /opt/chromium/chrome-linux/chrome /opt/google/chrome/chrome',
    erase_after_idle_days: null,
    internet: false,
    volume_gib: 10,
    volume_max_gib: 50,
  },
  version: 'v-browser-1',
  packages_version: 'pv-browser-1',
  within_caps: true,
  requested_by: { kind: 'app', id: 'app-bot' },
  requested_at: '2026-11-02T10:00:00Z',
  decided_by: null,
  decided_at: null,
  reason: null,
  profile_id: null,
}

/** An extension's request, through its manifest, over the platform's limits. */
export const EXTENSION_REQUEST: VmProfileRequest = {
  ...BROWSER_REQUEST,
  id: 'r-ext',
  subject: { kind: 'extension', id: 'acme.browser', name: 'Browser tools' },
  requested: { ...BROWSER_REQUEST.requested, memory_mib: 65536, setup_script: null },
  requested_by: { kind: 'service', id: 'svc-1' },
  within_caps: false,
}

/** An app asking for internet access as well: a security grant the admin must see. */
export const INTERNET_REQUEST: VmProfileRequest = {
  ...BROWSER_REQUEST,
  id: 'r-web',
  subject: { kind: 'app', id: 'app-web', name: 'Web researcher' },
  requested: { ...BROWSER_REQUEST.requested, internet: true },
  version: 'v-web-1',
}
