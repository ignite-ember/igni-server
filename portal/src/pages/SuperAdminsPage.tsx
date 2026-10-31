import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Banner, Button, FormControl, IconButton, PageHeader, Stack, Text, TextInput } from '@primer/react'
import { Blankslate } from '@primer/react/experimental'
import { ShieldCheckIcon, TrashIcon } from '@primer/octicons-react'
import client from '../api/client'
import { refusalOf } from '../api/refusal'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import type { SuperAdmin, UserMe } from '../types'

/**
 * Who owns this install. Super admins create organisations, make other
 * super admins, and change settings that belong to the whole deployment.
 * The first account a deployment creates is one; the last one cannot be
 * removed.
 */
export function SuperAdminsPage() {
  useDocumentTitle('Super admins')
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('')

  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  })
  const admins = useQuery({
    queryKey: ['super-admins'],
    queryFn: () => client.get<SuperAdmin[]>('/portal/super-admins').then((r) => r.data),
    enabled: me.data?.is_super_admin === true,
  })
  const make = useMutation({
    mutationFn: (address: string) => client.post<SuperAdmin>('/portal/super-admins', { email: address }),
    onSuccess: () => {
      setEmail('')
      queryClient.invalidateQueries({ queryKey: ['super-admins'] })
    },
  })
  const unmake = useMutation({
    mutationFn: (userId: string) => client.delete(`/portal/super-admins/${userId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['super-admins'] }),
  })

  if (me.data && me.data.is_super_admin !== true) {
    return (
      <Blankslate>
        <Blankslate.Heading>Super admins only</Blankslate.Heading>
        <Blankslate.Description>This page is for the people who own this igni install.</Blankslate.Description>
      </Blankslate>
    )
  }

  return (
    <Stack gap="spacious">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.LeadingVisual>
            <ShieldCheckIcon />
          </PageHeader.LeadingVisual>
          <PageHeader.Title>Super admins</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text>They create organisations, make other super admins, and change settings for the whole install.</Text>
        </PageHeader.Description>
      </PageHeader>

      {(make.isError || unmake.isError) && (
        <Banner variant="critical" title="Not changed" description={refusalOf(make.error ?? unmake.error)} />
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (email.trim()) make.mutate(email.trim())
        }}
      >
        <Stack direction="horizontal" align="end" gap="condensed">
          <FormControl>
            <FormControl.Label>Make a super admin</FormControl.Label>
            <FormControl.Caption>The address of an existing account. They need to sign up first.</FormControl.Caption>
            <TextInput
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="name@company.com"
            />
          </FormControl>
          <Button type="submit" disabled={!email.trim() || make.isPending}>
            {make.isPending ? 'Adding…' : 'Add'}
          </Button>
        </Stack>
      </form>

      <Stack gap="condensed" role="list" aria-label="Super admins">
        {(admins.data ?? []).map((admin) => (
          <Stack key={admin.user_id} direction="horizontal" align="center" justify="space-between" role="listitem">
            <Stack gap="none">
              <Text weight="semibold">{admin.full_name || admin.email}</Text>
              {admin.full_name && <Text size="small">{admin.email}</Text>}
            </Stack>
            <IconButton
              icon={TrashIcon}
              variant="invisible"
              aria-label={`Remove ${admin.email ?? admin.user_id} as super admin`}
              onClick={() => unmake.mutate(admin.user_id)}
              disabled={unmake.isPending}
            />
          </Stack>
        ))}
      </Stack>
    </Stack>
  )
}
