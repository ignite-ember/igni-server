import { ShieldCheckIcon, ShieldIcon } from '@primer/octicons-react'

import type { TeamRole } from '../../types'

export interface RoleOption {
  role: TeamRole
  label: string
  icon: typeof ShieldCheckIcon
}

/** Every role an admin can hand out, in the order the menus list them. */
export const ROLE_OPTIONS: readonly RoleOption[] = [
  { role: 'admin', label: 'Admin', icon: ShieldCheckIcon },
  { role: 'member', label: 'Member', icon: ShieldIcon },
]

export function roleLabel(role: TeamRole): string {
  return ROLE_OPTIONS.find((option) => option.role === role)?.label ?? role
}
