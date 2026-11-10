import { ConfirmationDialog, Text } from '@primer/react'

import type { TeamMember } from '../../types'

/**
 * Sign-out-everywhere confirmation. Confirmed rather than immediate: it is
 * not destructive — they can sign in again — but it does end whatever they
 * were in the middle of on every machine, and it is one menu item away
 * from "Deactivate".
 */
export function RevokeSessionsDialog({
  member,
  onClose,
}: {
  member: TeamMember
  onClose: (confirmed: boolean) => void
}) {
  return (
    <ConfirmationDialog
      title="Sign out of every device?"
      confirmButtonContent="Sign out everywhere"
      cancelButtonContent="Cancel"
      width="large"
      className="confirm-dialog-lg"
      onClose={(gesture) => onClose(gesture === 'confirm')}
    >
      <Text size="medium">
        Every token <Text weight="semibold">{member.full_name ?? member.email}</Text> currently holds — portal, CLI
        and editor — stops working immediately. They keep their account and their access to this organisation, and
        can sign in again whenever they like.
      </Text>
    </ConfirmationDialog>
  )
}
