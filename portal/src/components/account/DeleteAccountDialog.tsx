import { useState } from 'react'
import { Dialog, FormControl, Stack, Text, TextInput } from '@primer/react'
import { useMutation } from '@tanstack/react-query'

import client from '../../api/client'
import { useAuth } from '../../auth/useAuth'
import type { DeleteAccountResponse } from '../../types'

export function DeleteAccountDialog({
  email,
  onClose,
  returnFocusRef,
}: {
  email: string
  onClose: () => void
  returnFocusRef: React.RefObject<HTMLButtonElement | null>
}) {
  const [confirm, setConfirm] = useState('')
  const { logout } = useAuth()

  const deleteMutation = useMutation({
    mutationFn: () => client.delete<DeleteAccountResponse>('/portal/me').then((r) => r.data),
    onSuccess: () => {
      onClose()
      logout()
    },
  })

  const canDelete = confirm === email

  return (
    <Dialog
      title="Delete account"
      onClose={onClose}
      returnFocusRef={returnFocusRef as React.RefObject<HTMLElement>}
      footerButtons={[
        { content: 'Cancel', onClick: onClose },
        {
          content: deleteMutation.isPending ? 'Deleting...' : 'Delete account',
          buttonType: 'danger',
          onClick: () => deleteMutation.mutate(),
          disabled: !canDelete || deleteMutation.isPending,
        },
      ]}
    >
      <Stack gap="normal">
        <Text>
          This will permanently delete your account, remove you from all organizations,
          and erase your data. This action cannot be undone.
        </Text>
        <FormControl>
          <FormControl.Label>
            Type <strong>{email}</strong> to confirm
          </FormControl.Label>
          <TextInput
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            placeholder={email}
            block
            autoFocus
          />
        </FormControl>
      </Stack>
    </Dialog>
  )
}
