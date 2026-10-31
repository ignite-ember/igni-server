import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Banner, Button, Label, PageHeader, Stack, Text } from '@primer/react'
import { Blankslate, DataTable, Table } from '@primer/react/experimental'
import { KeyIcon } from '@primer/octicons-react'
import client from '../api/client'
import { refusalOf } from '../api/refusal'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import type { OrgAppList, TeamMemberList, UserMe, VmCredential } from '../types'

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString() : 'never'
}

/**
 * The credentials producers' VMs hold of their own. Each rotates on every
 * use and stops after 90 days unused; revoking one stops its VM getting
 * tokens until the person signs in again or an admin re-issues the app's.
 * Yours, or — for an org admin — the organisation's people's and apps'.
 */
export function VmCredentialsPage() {
  useDocumentTitle('VM credentials')
  const queryClient = useQueryClient()
  const [feedback, setFeedback] = useState<{ ok: boolean; message: string } | null>(null)

  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  })
  const isAdmin = me.data?.role === 'admin'
  const credentials = useQuery({
    queryKey: ['vm-credentials'],
    queryFn: () => client.get<{ credentials: VmCredential[] }>('/portal/vm-credentials').then((r) => r.data.credentials),
  })
  const team = useQuery({
    queryKey: ['team'],
    queryFn: () => client.get<TeamMemberList>('/portal/team').then((r) => r.data),
    enabled: isAdmin,
  })
  const apps = useQuery({
    queryKey: ['apps'],
    queryFn: () => client.get<OrgAppList>('/portal/apps').then((r) => r.data),
    enabled: isAdmin,
  })
  const revoke = useMutation({
    mutationFn: (credentialId: string) => client.delete(`/portal/vm-credentials/${credentialId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['vm-credentials'] })
      setFeedback({ ok: true, message: 'Revoked. That VM gets no more tokens with it.' })
    },
    onError: (error) => setFeedback({ ok: false, message: refusalOf(error) }),
  })

  const nameOf = (credential: VmCredential): string => {
    if (credential.producer.kind === 'app') {
      const app = apps.data?.apps.find((a) => a.app_id === credential.producer.id)
      return `App: ${app?.name ?? credential.producer.id}`
    }
    if (credential.producer.id === me.data?.user_id) return 'You'
    const person = team.data?.members.find((m) => m.user_id === credential.producer.id)
    return person?.full_name || person?.email || credential.producer.id
  }

  const list = credentials.data ?? []

  return (
    <Stack gap="spacious">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.LeadingVisual>
            <KeyIcon />
          </PageHeader.LeadingVisual>
          <PageHeader.Title>VM credentials</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text>
            Each cloud VM holds its own credential, so it can work with nobody present. It changes every time it is
            used and stops after 90 days unused.
          </Text>
        </PageHeader.Description>
      </PageHeader>

      {feedback && (
        <Banner
          variant={feedback.ok ? 'success' : 'critical'}
          title={feedback.ok ? 'Done' : 'Not revoked'}
          description={feedback.message}
          onDismiss={() => setFeedback(null)}
        />
      )}

      {list.length === 0 && !credentials.isLoading ? (
        <Blankslate>
          <Blankslate.Heading>No VM credentials</Blankslate.Heading>
          <Blankslate.Description>
            A person&apos;s VM gets one when they sign in through the cloud; an app&apos;s when an admin enables the
            cloud for it.
          </Blankslate.Description>
        </Blankslate>
      ) : (
        <Table.Container>
          <DataTable
            aria-label="VM credentials"
            data={list.map((credential) => ({ id: credential.credential_id, ...credential }))}
            columns={[
              { header: 'VM of', field: 'producer', rowHeader: true, renderCell: (row) => nameOf(row) },
              { header: 'Issued', field: 'created_at', renderCell: (row) => when(row.created_at) },
              { header: 'Last used', field: 'last_used_at', renderCell: (row) => when(row.last_used_at) },
              { header: 'Expires if unused', field: 'idle_expires_at', renderCell: (row) => when(row.idle_expires_at) },
              {
                header: 'State',
                field: 'awaiting_pickup',
                renderCell: (row) =>
                  row.awaiting_pickup ? <Label variant="attention">waiting for its VM</Label> : <Label>in use</Label>,
              },
              {
                header: '',
                field: 'generation',
                renderCell: (row) => (
                  <Button
                    size="small"
                    variant="danger"
                    onClick={() => revoke.mutate(row.credential_id)}
                    aria-label={`Revoke the credential of ${nameOf(row)}`}
                  >
                    Revoke
                  </Button>
                ),
              },
            ]}
          />
        </Table.Container>
      )}
    </Stack>
  )
}
