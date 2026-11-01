import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { Banner, Button, ConfirmationDialog, Label, PageHeader, Stack, Text } from '@primer/react'
import { Blankslate, DataTable, Table } from '@primer/react/experimental'
import { TrashIcon } from '@primer/octicons-react'
import client from '../api/client'
import { refusalOf } from '../api/refusal'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import type { Erasure, ErasureList, UserMe } from '../types'

/** How often the list is read again while something is being erased. */
const POLL_MS = 3000

function day(iso: string | null): string {
  return iso ? new Date(iso).toLocaleDateString() : '—'
}

function stateLabel(row: Erasure) {
  switch (row.state) {
    case 'held':
      return <Label variant="attention">Held (legal hold)</Label>
    case 'erasing':
      return <Label variant="accent">Erasing…</Label>
    case 'erased':
      return <Label variant="success">Erased {day(row.erased_at)}</Label>
    default:
      return <Label>Erase on {day(row.erase_at)}</Label>
  }
}

const KINDS: Record<Erasure['producer']['kind'], string> = {
  person: 'Former member',
  app: 'Discarded app',
  space: "An app's space",
}

/**
 * What is on its way to erasure: former members' VMs (kept for the
 * organisation's retention, unless discarded now), discarded apps and
 * spaces (erased at once in the background), and what a legal hold keeps.
 * A current member's VM is never here: it is not discarded.
 */
export function ErasuresPage() {
  useDocumentTitle('Erasures')
  const queryClient = useQueryClient()
  const [params] = useSearchParams()
  const [confirming, setConfirming] = useState<Erasure | null>(null)
  const [feedback, setFeedback] = useState<{ variant: 'success' | 'critical'; message: string } | null>(null)

  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  })
  const orgId = params.get('org') ?? me.data?.org_id ?? ''
  const list = useQuery({
    queryKey: ['erasures', orgId],
    queryFn: () => client.get<ErasureList>(`/portal/orgs/${orgId}/erasures`).then((r) => r.data),
    enabled: !!orgId,
    refetchInterval: (query) =>
      (query.state.data?.erasures ?? []).some((e) => e.state === 'erasing') ? POLL_MS : false,
  })
  const discard = useMutation({
    mutationFn: (row: Erasure) =>
      client.post(`/portal/orgs/${orgId}/members/${row.producer.id}/discard`, { incarnation: row.incarnation }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['erasures', orgId] })
      setFeedback({ variant: 'success', message: 'Discarded. Their VM is being erased now.' })
    },
    onError: (error) => setFeedback({ variant: 'critical', message: refusalOf(error) }),
  })

  if (list.isError) {
    return (
      <Blankslate>
        <Blankslate.Heading>Organisation admins only</Blankslate.Heading>
        <Blankslate.Description>{refusalOf(list.error, 'This page is not available to you.')}</Blankslate.Description>
      </Blankslate>
    )
  }

  const rows = (list.data?.erasures ?? []).map((e) => ({ ...e, id: `${e.producer.kind}:${e.producer.id}:${e.incarnation}` }))

  return (
    <Stack gap="spacious">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.LeadingVisual>
            <TrashIcon />
          </PageHeader.LeadingVisual>
          <PageHeader.Title>Erasures</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text>
            Former members&apos; VMs are kept for your organisation&apos;s retention, then erased: memory, snapshots,
            volume and keys. Discarded apps are erased at once, in the background. A legal hold keeps either until it
            is released. A current member&apos;s VM is never discarded.
          </Text>
        </PageHeader.Description>
      </PageHeader>

      {feedback && (
        <Banner
          variant={feedback.variant}
          title={feedback.variant === 'success' ? 'Done' : 'Not discarded'}
          description={feedback.message}
          onDismiss={() => setFeedback(null)}
        />
      )}

      {rows.length === 0 ? (
        <Text size="small">{list.data ? 'Nothing is waiting for erasure.' : 'Loading…'}</Text>
      ) : (
        <Table.Container>
          <DataTable
            aria-label="Erasures"
            data={rows}
            columns={[
              {
                header: 'Who or what',
                field: 'name',
                rowHeader: true,
                renderCell: (row) => (
                  <Stack gap="none">
                    <Text weight="semibold">{row.name ?? row.producer.id}</Text>
                    <Text size="small">{KINDS[row.producer.kind]}</Text>
                  </Stack>
                ),
              },
              { header: 'Removed', field: 'removed_at', renderCell: (row) => day(row.removed_at) },
              { header: 'State', field: 'state', renderCell: (row) => stateLabel(row) },
              {
                header: '',
                field: 'incarnation',
                renderCell: (row) =>
                  row.producer.kind === 'person' && row.state === 'scheduled' ? (
                    <Button
                      size="small"
                      variant="danger"
                      onClick={() => setConfirming(row)}
                      aria-label={`Discard now ${row.name ?? row.producer.id}`}
                    >
                      Discard now
                    </Button>
                  ) : null,
              },
            ]}
          />
        </Table.Container>
      )}

      {confirming && (
        <ConfirmationDialog
          title={`Discard ${confirming.name ?? 'this former member'}'s VM now?`}
          confirmButtonContent="Discard now"
          cancelButtonContent="Cancel"
          confirmButtonType="danger"
          onClose={(gesture) => {
            const chosen = confirming
            setConfirming(null)
            if (gesture === 'confirm') discard.mutate(chosen)
          }}
        >
          <Stack gap="normal">
            <Text>
              Erase this former member&apos;s VM now instead of on {day(confirming.erase_at)}? Their memory, snapshots,
              volume and keys are erased in the background and cannot be recovered.
            </Text>
            {confirming.exports > 0 && (
              <Text weight="semibold">
                An export of this person&apos;s data is pending or ready; discarding now ends the chance to take another.
              </Text>
            )}
          </Stack>
        </ConfirmationDialog>
      )}
    </Stack>
  )
}
