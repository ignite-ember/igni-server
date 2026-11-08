import { useRef, useState } from 'react'
import { Banner, Button, FormControl, Link, Stack, Text, TextInput } from '@primer/react'
import { Blankslate } from '@primer/react/experimental'
import { OrganizationIcon } from '@primer/octicons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link as RouterLink } from 'react-router-dom'
import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import type { DeploymentOrg } from '../../types'
import { CreateOrgDialog } from '../org/CreateOrgDialog'

/**
 * The bootstrap account's way onto its company's domain: prove an
 * address there, and the account moves onto it — the old address is
 * gone — and joins the organisation that owns it.
 */
function CompanyAddress() {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const send = useMutation({
    mutationFn: (address: string) =>
      client.post<{ message: string }>('/portal/me/company-address/code', { email: address }).then((r) => r.data),
  })
  const confirm = useMutation({
    mutationFn: () => client.post('/portal/me/company-address', { email: email.trim(), code: code.trim() }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['me'] }),
  })

  return (
    <Stack gap="condensed">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (email.trim()) send.mutate(email.trim())
        }}
      >
        <Stack direction="horizontal" align="end" gap="condensed">
          <FormControl>
            <FormControl.Label>Your company address</FormControl.Label>
            <TextInput type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@acme.com" />
          </FormControl>
          <Button type="submit" disabled={!email.trim() || send.isPending}>
            Send code
          </Button>
        </Stack>
      </form>
      {send.isSuccess && (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (code.trim()) confirm.mutate()
          }}
        >
          <Stack direction="horizontal" align="end" gap="condensed">
            <FormControl>
              <FormControl.Label>Code</FormControl.Label>
              <FormControl.Caption>{send.data.message}</FormControl.Caption>
              <TextInput value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" />
            </FormControl>
            <Button type="submit" variant="primary" disabled={!code.trim() || confirm.isPending}>
              Use this address
            </Button>
          </Stack>
        </form>
      )}
      {(send.isError || confirm.isError) && (
        <Banner variant="critical" title="Not changed" description={refusalOf(send.error ?? confirm.error)} />
      )}
    </Stack>
  )
}

/**
 * What an account in no organisation sees instead of an org-scoped page.
 *
 * Everybody else joins the organisation that owns their address's domain
 * as their account is made, so in practice this is the bootstrap
 * account: the install's first, made before any organisation owned a
 * domain. It creates the organisation, then either moves onto an address
 * on its domain or makes a colleague super admin and deletes itself.
 */
export function NoOrganisation({ isSuperAdmin }: { isSuperAdmin: boolean }) {
  const [creating, setCreating] = useState(false)
  const createRef = useRef<HTMLButtonElement>(null)
  const orgs = useQuery({
    queryKey: ['deployment-organizations'],
    queryFn: () => client.get<DeploymentOrg[]>('/portal/deployment/organizations').then((r) => r.data),
    enabled: isSuperAdmin,
  })
  const anyOrganisation = (orgs.data ?? []).length > 0

  return (
    <Stack gap="normal" padding="spacious">
      <Blankslate border>
        <Blankslate.Visual>
          <OrganizationIcon size={24} />
        </Blankslate.Visual>
        <Blankslate.Heading>You're not in an organisation yet</Blankslate.Heading>
        <Blankslate.Description>
          {!isSuperAdmin && (
            <Text>Ask an admin of your organisation to invite you, or a super admin to create one.</Text>
          )}
          {isSuperAdmin && !anyOrganisation && (
            <Text>
              Create your company's organisation and give it your email domains. Everybody who signs up with an
              address on them joins it.
            </Text>
          )}
          {isSuperAdmin && anyOrganisation && (
            <Text>
              This account was made before the organisation existed. Move it onto your address at the company, or{' '}
              <Link as={RouterLink} to="/super-admins">
                make a colleague a super admin
              </Link>{' '}
              and delete this account.
            </Text>
          )}
        </Blankslate.Description>
        {isSuperAdmin && !anyOrganisation && orgs.isSuccess && (
          <Button ref={createRef} variant="primary" onClick={() => setCreating(true)}>
            Create organization
          </Button>
        )}
      </Blankslate>
      {isSuperAdmin && anyOrganisation && <CompanyAddress />}
      {creating && <CreateOrgDialog onClose={() => setCreating(false)} returnFocusRef={createRef} />}
    </Stack>
  )
}
