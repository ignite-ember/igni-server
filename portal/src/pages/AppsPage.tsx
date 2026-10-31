import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link as RouterLink } from 'react-router-dom'
import { Banner, Button, FormControl, Link, PageHeader, Select, Stack, Text, Textarea, TextInput } from '@primer/react'
import { Blankslate, DataTable, Table } from '@primer/react/experimental'
import { AppsIcon } from '@primer/octicons-react'
import client from '../api/client'
import { refusalOf } from '../api/refusal'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import { parsePublicJwks } from '../lib/publicJwks'
import type { OrgApp, OrgAppList, TeamMemberList, UserMe } from '../types'

function RegisterAppForm({ onRegistered }: { onRegistered: (app: OrgApp) => void }) {
  const [name, setName] = useState('')
  const [owner, setOwner] = useState('')
  const [jwk, setJwk] = useState('')
  const [problem, setProblem] = useState<string | null>(null)

  const team = useQuery({
    queryKey: ['team'],
    queryFn: () => client.get<TeamMemberList>('/portal/team').then((r) => r.data),
  })
  const register = useMutation({
    mutationFn: (body: { name: string; owner_user_id: string; jwks: { keys: Record<string, unknown>[] } }) =>
      client.post<OrgApp>('/portal/apps', body).then((r) => r.data),
    onSuccess: (app) => {
      setName('')
      setJwk('')
      setProblem(null)
      onRegistered(app)
    },
    onError: (error) => setProblem(refusalOf(error, 'The app was not registered.')),
  })

  const members = (team.data?.members ?? []).filter((m) => m.status === 'active')

  return (
    <form
      aria-label="Register an app"
      onSubmit={(e) => {
        e.preventDefault()
        const jwks = parsePublicJwks(jwk)
        if (typeof jwks === 'string') {
          setProblem(jwks)
          return
        }
        register.mutate({ name: name.trim(), owner_user_id: owner, jwks })
      }}
    >
      <Stack gap="normal">
        <Text weight="semibold">Register an app</Text>
        <Stack direction="horizontal" gap="normal" wrap="wrap">
          <FormControl required>
            <FormControl.Label>Name</FormControl.Label>
            <TextInput value={name} onChange={(e) => setName(e.target.value)} placeholder="Review bot" />
          </FormControl>
          <FormControl required>
            <FormControl.Label>Owner</FormControl.Label>
            <Select value={owner} onChange={(e) => setOwner(e.target.value)}>
              <Select.Option value="">Choose a member</Select.Option>
              {members.map((m) => (
                <Select.Option key={m.user_id} value={m.user_id}>
                  {m.full_name || m.email}
                </Select.Option>
              ))}
            </Select>
          </FormControl>
        </Stack>
        <FormControl required>
          <FormControl.Label>Public key</FormControl.Label>
          <FormControl.Caption>
            The app generates its own key pair and prints the public half as a JWK. Paste that; the private key never
            leaves the app.
          </FormControl.Caption>
          <Textarea
            value={jwk}
            onChange={(e) => setJwk(e.target.value)}
            rows={4}
            block
            placeholder='{"kty": "RSA", "n": "...", "e": "AQAB", "kid": "..."}'
          />
        </FormControl>
        {problem && <Banner variant="critical" title="Not registered" description={problem} />}
        <Stack direction="horizontal">
          <Button type="submit" variant="primary" disabled={!name.trim() || !owner || !jwk.trim() || register.isPending}>
            {register.isPending ? 'Registering…' : 'Register'}
          </Button>
        </Stack>
      </Stack>
    </form>
  )
}

/**
 * The organisation's apps: producers that are not people. An app acts
 * only as itself, is in no group, and gets tokens with its own key pair
 * — the same mechanism a service uses.
 */
export function AppsPage() {
  useDocumentTitle('Apps')
  const queryClient = useQueryClient()
  const [registered, setRegistered] = useState<OrgApp | null>(null)

  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  })
  const apps = useQuery({
    queryKey: ['apps'],
    queryFn: () => client.get<OrgAppList>('/portal/apps').then((r) => r.data),
  })

  const isAdmin = me.data?.role === 'admin'

  if (me.data && !isAdmin) {
    return (
      <Blankslate>
        <Blankslate.Heading>Organisation admins only</Blankslate.Heading>
        <Blankslate.Description>Apps are registered by an admin of the organisation.</Blankslate.Description>
      </Blankslate>
    )
  }

  const list = apps.data?.apps ?? []

  return (
    <Stack gap="spacious">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.LeadingVisual>
            <AppsIcon />
          </PageHeader.LeadingVisual>
          <PageHeader.Title>Apps</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text>
            An app acts only as itself: it is in no group, never acts for a person, and gets only the extensions you
            switch on for it.
          </Text>
        </PageHeader.Description>
      </PageHeader>

      {registered && (
        <Banner
          variant="success"
          title={`${registered.name} is registered`}
          description={`Its client id is ${registered.client_id}. Compare the key thumbprint ${registered.thumbprint} with the one the app printed.`}
          onDismiss={() => setRegistered(null)}
        />
      )}

      <RegisterAppForm
        onRegistered={(app) => {
          setRegistered(app)
          queryClient.invalidateQueries({ queryKey: ['apps'] })
        }}
      />

      {list.length === 0 && !apps.isLoading ? (
        <Blankslate>
          <Blankslate.Heading>No apps yet</Blankslate.Heading>
          <Blankslate.Description>Register one above with the public key it generated.</Blankslate.Description>
        </Blankslate>
      ) : (
        <Table.Container>
          <DataTable
            aria-label="Apps"
            data={list.map((app) => ({ id: app.app_id, ...app }))}
            columns={[
              {
                header: 'App',
                field: 'name',
                rowHeader: true,
                renderCell: (row) => (
                  <Link as={RouterLink} to={`/apps/${row.app_id}`}>
                    {row.name}
                  </Link>
                ),
              },
              { header: 'Owner', field: 'owner_email', renderCell: (row) => row.owner_email ?? '—' },
              { header: 'Client id', field: 'client_id', renderCell: (row) => <code>{row.client_id}</code> },
              { header: 'Key thumbprint', field: 'thumbprint', renderCell: (row) => <code>{row.thumbprint}</code> },
            ]}
          />
        </Table.Container>
      )}
    </Stack>
  )
}
