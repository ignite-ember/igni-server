import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { Banner, Button, FormControl, PageHeader, Select, Stack, Text, Textarea, ToggleSwitch } from '@primer/react'
import { Blankslate, DataTable, SkeletonText, Table } from '@primer/react/experimental'
import { AppsIcon } from '@primer/octicons-react'
import client from '../api/client'
import { refusalOf } from '../api/refusal'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import { parsePublicJwks } from '../lib/publicJwks'
import type { GroupExtensionList, OrgApp, UserMe, VmProfilesOverview } from '../types'

/**
 * One app: its own list of extensions (off until switched on here, and
 * nothing to do with any group's), its VM profile, a key rotation, and
 * removing it.
 */
export function AppDetailPage() {
  const { appId = '' } = useParams<{ appId: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [feedback, setFeedback] = useState<{ variant: 'success' | 'critical'; message: string } | null>(null)
  const [newKey, setNewKey] = useState('')

  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  })
  const app = useQuery({
    queryKey: ['app', appId],
    queryFn: () => client.get<OrgApp>(`/portal/apps/${appId}`).then((r) => r.data),
  })
  useDocumentTitle(app.data?.name ?? 'App')
  const extensions = useQuery({
    queryKey: ['app-extensions', appId],
    queryFn: () => client.get<GroupExtensionList>(`/portal/apps/${appId}/extensions`).then((r) => r.data),
  })
  const orgId = me.data?.org_id ?? ''
  const profiles = useQuery({
    queryKey: ['vm-profiles', orgId],
    queryFn: () => client.get<VmProfilesOverview>(`/portal/orgs/${orgId}/vm-profiles`).then((r) => r.data),
    enabled: !!orgId,
  })

  const done = (message: string) => setFeedback({ variant: 'success', message })
  const failed = (error: unknown) => setFeedback({ variant: 'critical', message: refusalOf(error) })

  const setEnabled = useMutation({
    mutationFn: ({ extensionId, enabled }: { extensionId: string; enabled: boolean }) =>
      client.put(`/portal/apps/${appId}/extensions/${extensionId}`, { enabled }),
    onSuccess: (_data, { enabled }) => {
      queryClient.invalidateQueries({ queryKey: ['app-extensions', appId] })
      done(enabled ? 'Extension switched on for this app' : 'Extension switched off for this app')
    },
    onError: failed,
  })
  const assign = useMutation({
    mutationFn: (profileId: string | null) =>
      client.put(`/portal/orgs/${orgId}/apps/${appId}/vm-profile`, { profile_id: profileId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['app', appId] })
      queryClient.invalidateQueries({ queryKey: ['vm-profiles', orgId] })
      done('VM profile changed')
    },
    onError: failed,
  })
  const rotate = useMutation({
    mutationFn: (jwks: { keys: Record<string, unknown>[] }) => client.put<OrgApp>(`/portal/apps/${appId}/keys`, { jwks }),
    onSuccess: () => {
      setNewKey('')
      queryClient.invalidateQueries({ queryKey: ['app', appId] })
      done('Keys replaced. The old key no longer gets tokens.')
    },
    onError: failed,
  })
  const enableCloud = useMutation({
    mutationFn: () => client.post(`/portal/apps/${appId}/vm-credential`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['vm-credentials'] })
      done("Issued. The node agent hands it to the app's VM when it creates it; nobody sees it.")
    },
    onError: failed,
  })
  const remove = useMutation({
    mutationFn: () => client.delete(`/portal/apps/${appId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['apps'] })
      navigate('/apps')
    },
    onError: failed,
  })

  if (app.isError) {
    return (
      <Blankslate>
        <Blankslate.Heading>App not found</Blankslate.Heading>
        <Blankslate.Description>It may have been removed, or it belongs to another organisation.</Blankslate.Description>
      </Blankslate>
    )
  }
  if (!app.data) return <SkeletonText size="bodyMedium" maxWidth={300} />

  const list = extensions.data?.extensions ?? []

  return (
    <Stack gap="spacious">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.LeadingVisual>
            <AppsIcon />
          </PageHeader.LeadingVisual>
          <PageHeader.Title>{app.data.name}</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text>
            Client id <code>{app.data.client_id}</code> · owned by {app.data.owner_email ?? 'nobody'} · key{' '}
            <code>{app.data.thumbprint}</code>
          </Text>
        </PageHeader.Description>
      </PageHeader>

      {feedback && (
        <Banner
          variant={feedback.variant}
          title={feedback.variant === 'success' ? 'Saved' : 'Not changed'}
          description={feedback.message}
          onDismiss={() => setFeedback(null)}
        />
      )}

      <Stack gap="normal">
        <Text weight="semibold">Extensions</Text>
        <Text size="medium">
          An approved extension reaches this app only once you switch it on here. Groups&apos; switches do not apply to
          apps.
        </Text>
        {list.length === 0 ? (
          <Text size="small">No approved extensions yet.</Text>
        ) : (
          <Table.Container>
            <DataTable
              aria-label="App extensions"
              data={list.map((extension) => ({ id: extension.extension_id, ...extension }))}
              columns={[
                {
                  header: 'Extension',
                  field: 'name',
                  rowHeader: true,
                  renderCell: (row) => (
                    <Text weight="semibold" id={`app-extension-${row.extension_id}`}>
                      {row.name}
                    </Text>
                  ),
                },
                {
                  header: 'This app',
                  field: 'enabled',
                  renderCell: (row) => (
                    <ToggleSwitch
                      size="small"
                      aria-labelledby={`app-extension-${row.extension_id}`}
                      checked={row.enabled}
                      loading={setEnabled.isPending && setEnabled.variables?.extensionId === row.extension_id}
                      onClick={() => setEnabled.mutate({ extensionId: row.extension_id, enabled: !row.enabled })}
                    />
                  ),
                },
              ]}
            />
          </Table.Container>
        )}
      </Stack>

      <FormControl>
        <FormControl.Label>VM profile</FormControl.Label>
        <FormControl.Caption>What this app&apos;s microVM gets in the cloud.</FormControl.Caption>
        <Select
          value={app.data.vm_profile_id ?? ''}
          onChange={(e) => assign.mutate(e.target.value || null)}
          disabled={!profiles.data || assign.isPending}
        >
          <Select.Option value="">The organisation&apos;s default</Select.Option>
          {(profiles.data?.profiles ?? [])
            .filter((p) => !p.is_default)
            .map((p) => (
              <Select.Option key={p.id} value={p.id}>
                {p.name}
              </Select.Option>
            ))}
        </Select>
      </FormControl>

      <form
        aria-label="Replace keys"
        onSubmit={(e) => {
          e.preventDefault()
          const jwks = parsePublicJwks(newKey)
          if (typeof jwks === 'string') setFeedback({ variant: 'critical', message: jwks })
          else rotate.mutate(jwks)
        }}
      >
        <Stack gap="condensed">
          <FormControl>
            <FormControl.Label>Replace keys</FormControl.Label>
            <FormControl.Caption>The app&apos;s new public key. Its old key stops getting tokens at once.</FormControl.Caption>
            <Textarea value={newKey} onChange={(e) => setNewKey(e.target.value)} rows={3} block />
          </FormControl>
          <Stack direction="horizontal">
            <Button type="submit" disabled={!newKey.trim() || rotate.isPending}>
              Replace keys
            </Button>
          </Stack>
        </Stack>
      </form>

      <Stack gap="condensed">
        <Text weight="semibold">Cloud</Text>
        <Text size="small">
          The app&apos;s VM holds a credential of its own, picked up once by the node agent. Issuing again stops the
          old one at once.
        </Text>
        <Stack direction="horizontal">
          <Button onClick={() => enableCloud.mutate()} disabled={enableCloud.isPending}>
            Enable cloud / re-issue VM credential
          </Button>
        </Stack>
      </Stack>

      <Stack direction="horizontal">
        <Button variant="danger" onClick={() => remove.mutate()} disabled={remove.isPending}>
          Remove app
        </Button>
      </Stack>
    </Stack>
  )
}
