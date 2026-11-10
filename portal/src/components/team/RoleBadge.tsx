import { Label } from '@primer/react'
import { ShieldCheckIcon, ShieldIcon } from '@primer/octicons-react'

import type { TeamRole } from '../../types'

export function RoleBadge({ role }: { role: TeamRole }) {
  switch (role) {
    case 'admin':
      return (
        <Label variant="accent">
          <span className="role-badge-inner">
            <ShieldCheckIcon size={12} /> Admin
          </span>
        </Label>
      )
    case 'member':
      return (
        <Label>
          <span className="role-badge-inner">
            <ShieldIcon size={12} /> Member
          </span>
        </Label>
      )
  }
}
