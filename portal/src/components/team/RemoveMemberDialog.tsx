import { ConfirmationDialog, Text } from '@primer/react'

import type { TeamMember } from '../../types'

/** Removing a member cannot be undone, so it asks first. */
export function RemoveMemberDialog({ member, onClose }: { member: TeamMember; onClose: (confirmed: boolean) => void }) {
  return (
    <ConfirmationDialog
      title="Remove team member"
      confirmButtonContent="Remove"
      cancelButtonContent="Cancel"
      confirmButtonType="danger"
      width="large"
      className="confirm-dialog-lg"
      onClose={(gesture) => onClose(gesture === 'confirm')}
    >
      <Text size="medium">
        Are you sure you want to remove <Text weight="semibold">{member.full_name ?? member.email}</Text> from the
        team? This action cannot be undone.
      </Text>
    </ConfirmationDialog>
  )
}
