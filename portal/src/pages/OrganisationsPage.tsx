import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Banner, Button, IconButton, Label, PageHeader, Stack, Text, TextInput } from '@primer/react'
import { Blankslate } from '@primer/react/experimental'
import { OrganizationIcon, XIcon } from '@primer/octicons-react'
import client from '../api/client'
import { refusalOf } from '../api/refusal'
import { CreateOrgDialog } from '../components/org/CreateOrgDialog'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import type { DeploymentOrg, UserMe } from '../types'

function Domains({ org }: { org: DeploymentOrg }) {
  const queryClient = useQueryClient()
  const [domain, setDomain] = useState('')
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['deployment-organizations'] })
  const add = useMutation({
    mutationFn: (value: string) => client.post(`/portal/organizations/${org.id}/domains`, { domain: value }),
    onSuccess: () => {
      setDomain('')
      refresh()
    },
  })
  const remove = useMutation({
    mutationFn: (value: string) => client.delete(`/portal/organizations/${org.id}/domains/${value}`),
    onSuccess: refresh,
  })

  return (
    <Stack gap="condensed">
      <Stack direction="horizontal" gap="condensed" wrap="wrap" aria-label={`Domains of ${org.display_name}`}>
        {org.domains.map((d) => (
          <Label key={d}>
            {d}
            <IconButton
              icon={XIcon}
              size="small"
              variant="invisible"
              aria-label={`Remove ${d} from ${org.display_name}`}
              onClick={() => remove.mutate(d)}
            />
          </Label>
        ))}
      </Stack>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (domain.trim()) add.mutate(domain.trim())
        }}
      >
        <Stack direction="horizontal" gap="condensed">
          <TextInput
            aria-label={`Add a domain to ${org.display_name}`}
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
            placeholder="acme.co.uk"
          />
          <Button type="submit" disabled={!domain.trim() || add.isPending}>
            Add domain
          </Button>
        </Stack>
      </form>
      {(add.isError || remove.isError) && (
        <Banner variant="critical" title="Not changed" description={refusalOf(add.error ?? remove.error)} />
      )}
    </Stack>
  )
}

/**
 * Every organisation on the install, for the people who own it.
 *
 * An organisation owns email domains: somebody signing up with an
 * address on one joins it, and no two organisations share a domain. A
 * domain somebody's address is on cannot be removed.
 */
export function OrganisationsPage() {
  useDocumentTitle('Organisations')
  const [creating, setCreating] = useState(false)
  const createRef = useRef<HTMLButtonElement>(null)

  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  })
  const orgs = useQuery({
    queryKey: ['deployment-organizations'],
    queryFn: () => client.get<DeploymentOrg[]>('/portal/deployment/organizations').then((r) => r.data),
    enabled: me.data?.is_super_admin === true,
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
            <OrganizationIcon />
          </PageHeader.LeadingVisual>
          <PageHeader.Title>Organisations</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text>People join the organisation that owns their address&apos;s domain.</Text>
        </PageHeader.Description>
        <PageHeader.Actions>
          <Button ref={createRef} variant="primary" onClick={() => setCreating(true)}>
            Create organization
          </Button>
        </PageHeader.Actions>
      </PageHeader>


      <Stack gap="spacious" role="list" aria-label="Organisations">
        {(orgs.data ?? []).map((org) => (
          <Stack key={org.id} gap="condensed" role="listitem">
            <Text weight="semibold">{org.display_name}</Text>
            <Text size="small">
              {org.slug} · {org.members} {org.members === 1 ? 'member' : 'members'}
            </Text>
            <Domains org={org} />
          </Stack>
        ))}
      </Stack>
      {creating && <CreateOrgDialog onClose={() => setCreating(false)} returnFocusRef={createRef} />}
    </Stack>
  )
}
