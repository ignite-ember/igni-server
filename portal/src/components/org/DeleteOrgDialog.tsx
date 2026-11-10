import { useState } from 'react'
import { Dialog, FormControl, Stack, Text, TextInput } from '@primer/react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import client from '../../api/client'
import { useOrg } from '../../auth/useOrg'
import type { DeleteOrgResponse } from '../../types'

export function DeleteOrgDialog({
  orgId,
  orgName,
  onClose,
  returnFocusRef,
}: {
  orgId: string
  orgName: string
  onClose: () => void
  returnFocusRef: React.RefObject<HTMLButtonElement | null>
}) {
  const [confirm, setConfirm] = useState('')
  const queryClient = useQueryClient()
  const { organizations, switchOrg } = useOrg()

  const deleteMutation = useMutation({
    mutationFn: () => client.delete<DeleteOrgResponse>(`/portal/organizations/${orgId}`).then((r) => r.data),
    onSuccess: () => {
      const remaining = organizations.filter((o) => o.id !== orgId)
      if (remaining.length > 0) {
        switchOrg(remaining[0].id)
      }
      queryClient.invalidateQueries({ queryKey: ['me'] })
      onClose()
    },
  })

  const canDelete = confirm === orgName

  return (
    <Dialog
      title="Delete organization"
      onClose={onClose}
      returnFocusRef={returnFocusRef as React.RefObject<HTMLElement>}
      footerButtons={[
        { content: 'Cancel', onClick: onClose },
        {
          content: deleteMutation.isPending ? 'Deleting...' : 'Delete',
          buttonType: 'danger',
          onClick: () => deleteMutation.mutate(),
          disabled: !canDelete || deleteMutation.isPending,
        },
      ]}
    >
      <Stack gap="normal">
        <Text>
          This will permanently delete <strong>{orgName}</strong> and remove all members.
          This action cannot be undone.
        </Text>
        <FormControl>
          <FormControl.Label>
            Type <strong>{orgName}</strong> to confirm
          </FormControl.Label>
          <TextInput
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            placeholder={orgName}
            block
            autoFocus
          />
        </FormControl>
      </Stack>
    </Dialog>
  )
}
