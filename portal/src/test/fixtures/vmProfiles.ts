/** A VM profiles overview as the server answers it, for the cloud pages' tests. */
import type { VmProfile, VmProfilesOverview } from '../../types'

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
  within_caps: false,
}

export const OVERVIEW: VmProfilesOverview = {
  org_id: 'demo-org-001',
  caps: { max_memory_mib: 8192, max_vcpus: 4, max_idle_before_pause_s: 3600 },
  quota: { org_id: 'demo-org-001', awake_vm_seconds_per_month: null, enforced: false, updated_at: null },
  default_profile_id: 'p-default',
  profiles: [STANDARD, BROWSER],
  groups: [{ id: 'g-legal', name: 'Legal', vm_profile_id: null }],
  apps: [],
}
