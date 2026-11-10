import { useCallback, useState } from 'react'
import { Dialog, FormControl, Stack, Text, TextInput } from '@primer/react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import { useOrg } from '../../auth/useOrg'
import type { CreateOrgRequest, OrgChanged } from '../../types'

export function CreateOrgDialog({
  onClose,
  returnFocusRef,
}: {
  onClose: () => void
  returnFocusRef: React.RefObject<HTMLButtonElement | null>
}) {
  const [name, setName] = useState('')
  const [domains, setDomains] = useState('')
  const queryClient = useQueryClient()
  const { switchOrg } = useOrg()
  // Commas or spaces: people paste "acme.com, acme.co.uk" as often as one.
  const domainList = domains.split(/[\s,]+/).map((d) => d.trim()).filter(Boolean)

  const createMutation = useMutation({
    mutationFn: (body: CreateOrgRequest) =>
      client.post<OrgChanged>('/portal/organizations', body).then((r) => r.data),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['me'] })
      queryClient.invalidateQueries({ queryKey: ['deployment-organizations'] })
      switchOrg(data.id)
      onClose()
      setName('')
      setDomains('')
    },
  })

  const handleCreate = useCallback(() => {
    if (!name.trim() || domainList.length === 0) return
    createMutation.mutate({ display_name: name.trim(), domains: domainList })
  }, [name, domainList, createMutation])

  return (
    <Dialog
      title="Create organization"
      onClose={onClose}
      returnFocusRef={returnFocusRef as React.RefObject<HTMLElement>}
      footerButtons={[
        { content: 'Cancel', onClick: onClose },
        {
          content: createMutation.isPending ? 'Creating...' : 'Create',
          buttonType: 'primary',
          onClick: handleCreate,
          disabled: !name.trim() || domainList.length === 0 || createMutation.isPending,
        },
      ]}
    >
      <Stack gap="normal">
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
        <FormControl>
          <FormControl.Label>Email domains</FormControl.Label>
          <FormControl.Caption>
            People with an address on these domains join this organization when they sign up. No two
            organizations can share a domain.
          </FormControl.Caption>
          <TextInput
            value={domains}
            onChange={(e) => setDomains(e.target.value)}
            placeholder="acme.com"
            block
          />
        </FormControl>
        {createMutation.isError && (
          <Text size="small" className="color-fg-danger">
            {refusalOf(createMutation.error, 'The organization was not created.')}
          </Text>
        )}
      </Stack>
    </Dialog>
  )
}
