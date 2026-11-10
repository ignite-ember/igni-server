/**
 * The sidebar's routed nav groups, as data.
 *
 * Kept out of `Sidebar.tsx` because a component module that exports a
 * constant breaks fast refresh (`react-refresh/only-export-components`).
 */

import {
  type Icon,
  AppsIcon,
  ContainerIcon,
  CpuIcon,
  GearIcon,
  HomeIcon,
  KeyAsteriskIcon,
  KeyIcon,
  LawIcon,
  OrganizationIcon,
  PeopleIcon,
  ServerIcon,
  ShieldCheckIcon,
  ShieldLockIcon,
  TerminalIcon,
  TrashIcon,
} from '@primer/octicons-react'

export interface NavItem {
  label: string
  /** Route path, without the leading slash. */
  path: string
  icon: Icon
  /** Shown only to an admin of the active organisation. */
  adminOnly?: boolean
}

export const mainItems: NavItem[] = [{ label: 'Dashboard', path: 'dashboard', icon: HomeIcon }]

export const orgItems: NavItem[] = [
  { label: 'Team', path: 'team', icon: PeopleIcon },
  { label: 'Groups', path: 'groups', icon: PeopleIcon },
  { label: 'Inference', path: 'inference', icon: CpuIcon },
  // The cloud: apps are the organisation's other producers, VM profiles
  // what each producer's microVM gets. Both are org-admin only.
  { label: 'Apps', path: 'apps', icon: AppsIcon, adminOnly: true },
  { label: 'VM profiles', path: 'vm-profiles', icon: ContainerIcon, adminOnly: true },
  // Anybody may run a command in their own VM and read their own log.
  { label: 'Commands in VMs', path: 'vm-commands', icon: TerminalIcon },
  // Your own VM's credential, or — for an admin — everyone's.
  { label: 'VM credentials', path: 'vm-credentials', icon: KeyIcon },
  // Your access, handed to a program; an admin sees everybody's.
  { label: 'API keys', path: 'api-keys', icon: KeyAsteriskIcon },
  // Former members' and discarded apps' VMs on their way to erasure.
  { label: 'Erasures', path: 'erasures', icon: TrashIcon, adminOnly: true },
  // Every endpoint behind this page is `require_admin`, so the entry is
  // hidden for a member rather than shown and then refused — the flaw
  // F103 found on the login page, where three provider buttons appeared
  // whether or not the server would accept them.
  { label: 'Compliance', path: 'compliance', icon: ShieldLockIcon, adminOnly: true },
  { label: 'Settings', path: 'settings', icon: GearIcon },
]

/** The install's own pages: a super admin's only. */
export const deploymentItems: NavItem[] = [
  { label: 'Organisations', path: 'organisations', icon: OrganizationIcon },
  { label: 'Super admins', path: 'super-admins', icon: ShieldCheckIcon },
  { label: 'Services', path: 'services', icon: ServerIcon },
  { label: 'Cloud limits', path: 'cloud-limits', icon: ContainerIcon },
  // Whose licence this deployment runs on, and where to install another.
  { label: 'Licence', path: 'licence', icon: LawIcon },
]
