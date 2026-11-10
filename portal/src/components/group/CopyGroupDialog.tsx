import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Dialog, FormControl, Stack, Text, TextInput } from '@primer/react'

import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import type { OrgGroup, OrgGroupDetail } from '../../types'

/**
 * Copy a group: its entries, its extensions and its settings, under a
 * new name — and nobody in it, because each person is in exactly one
 * group and stays where they are. The copy is an ordinary group, so
 * copying the read-only Default one is how to get an editable version.
 */
export function CopyGroupDialog({
  group,
  returnFocusRef,
  onClose,
  onCopied,
}: {
  group: OrgGroupDetail
  returnFocusRef: React.RefObject<HTMLButtonElement | null>
  onClose: () => void
  onCopied: (copy: OrgGroup) => void
}) {
  const [name, setName] = useState(`${group.name} (copy)`)

  const copyMutation = useMutation({
    mutationFn: (copyName: string) =>
      client.post<OrgGroup>(`/portal/groups/${group.id}/copy`, { name: copyName }).then((r) => r.data),
    onSuccess: onCopied,
  })

  const submit = () => {
    if (!name.trim() || copyMutation.isPending) return
    copyMutation.mutate(name.trim())
  }

  return (
    <Dialog
      title="Copy group"
      onClose={onClose}
      returnFocusRef={returnFocusRef as React.RefObject<HTMLElement>}
      footerButtons={[
        { content: 'Cancel', onClick: onClose },
        {
          content: copyMutation.isPending ? 'Copying…' : 'Copy',
          buttonType: 'primary',
          onClick: submit,
          disabled: !name.trim() || copyMutation.isPending,
        },
      ]}
    >
      <Stack gap="normal">
        <Text size="small" className="group-detail-muted">
          The new group gets everything <Text weight="semibold">{group.name}</Text> gives its people — its
          configuration, extensions, model and branding. Nobody is moved into it.
        </Text>
        <FormControl>
          <FormControl.Label>Name</FormControl.Label>
          <TextInput value={name} onChange={(e) => setName(e.target.value)} block autoFocus />
        </FormControl>
        {copyMutation.isError && (
          <Text size="small" className="color-fg-danger">
            {refusalOf(copyMutation.error, 'The group was not copied.')}
          </Text>
        )}
      </Stack>
    </Dialog>
  )
}
