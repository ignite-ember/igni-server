import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Button, ConfirmationDialog, Text } from '@primer/react'

import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import type { OrgGroupEntry } from '../../types'

/**
 * Deleting the entry, behind a confirm. Everyone in the group loses it
 * on their next sync, which is worth a question first. A refusal goes
 * to `onRefused` in the server's own words, for the page to show;
 * `onDeleted` refreshes the group's entries and leaves.
 */
export function DeleteEntry({
  groupId,
  entry,
  disabled,
  onDeleted,
  onRefused,
}: {
  groupId: string
  entry: OrgGroupEntry
  disabled: boolean
  onDeleted: () => void
  onRefused: (reason: string) => void
}) {
  const [confirming, setConfirming] = useState(false)

  const remove = useMutation({
    mutationFn: () => client.delete(`/portal/groups/${groupId}/entries/${entry.id}`),
    onSuccess: () => onDeleted(),
    onError: (error) => onRefused(refusalOf(error, 'Failed to delete entry')),
  })

  return (
    <>
      <Button type="button" variant="danger" disabled={disabled || remove.isPending} onClick={() => setConfirming(true)}>
        Delete
      </Button>

      {confirming && (
        <ConfirmationDialog
          title="Delete entry"
          confirmButtonContent="Delete"
          cancelButtonContent="Cancel"
          confirmButtonType="danger"
          onClose={(gesture) => {
            if (gesture === 'confirm') remove.mutate()
            setConfirming(false)
          }}
        >
          <Text>
            Delete <Text weight="semibold">{entry.entry_name}</Text>? Everyone in this group loses it on
            their next sync.
          </Text>
        </ConfirmationDialog>
      )}
    </>
  )
}
