import { StateLabel } from '@primer/react'

import type { TeamMemberStatus } from '../../types'

export function StatusBadge({ status }: { status: TeamMemberStatus }) {
  switch (status) {
    case 'active':
      return <StateLabel status="issueOpened" variant="small">Active</StateLabel>
    case 'invited':
      // `issueDraft` renders a hollow dashed circle — empty in the middle,
      // signalling "pending acceptance" without implying success or failure.
      return <StateLabel status="issueDraft" variant="small">Invited</StateLabel>
    case 'deactivated':
      return <StateLabel status="issueClosedNotPlanned" variant="small">Deactivated</StateLabel>
  }
}
