import { useCallback, useState } from 'react'
import { Dialog, FormControl, TextInput } from '@primer/react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import client from '../../api/client'
import type { OrgChanged } from '../../types'

export function RenameOrgDialog({
  orgId,
  currentName,
  onClose,
  returnFocusRef,
}: {
  orgId: string
  currentName: string
  onClose: () => void
  returnFocusRef: React.RefObject<HTMLButtonElement | null>
}) {
  const [name, setName] = useState(currentName)
  const queryClient = useQueryClient()

  const renameMutation = useMutation({
    mutationFn: (displayName: string) =>
      client.patch<OrgChanged>(`/portal/organizations/${orgId}`, { display_name: displayName }).then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['me'] })
      onClose()
    },
  })

  const dirty = name.trim() !== currentName
  const handleRename = useCallback(() => {
    if (!name.trim() || !dirty) return
    renameMutation.mutate(name.trim())
  }, [name, dirty, renameMutation])

  return (
    <Dialog
      title="Rename organization"
      onClose={onClose}
      returnFocusRef={returnFocusRef as React.RefObject<HTMLElement>}
      footerButtons={[
        { content: 'Cancel', onClick: onClose },
        {
          content: renameMutation.isPending ? 'Saving...' : 'Save',
          buttonType: 'primary',
          onClick: handleRename,
          disabled: !dirty || !name.trim() || renameMutation.isPending,
        },
      ]}
    >
      <FormControl>
        <FormControl.Label>Organization name</FormControl.Label>
        <TextInput
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Acme Corp"
          block
          autoFocus
        />
      </FormControl>
    </Dialog>
  )
}
