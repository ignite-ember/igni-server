import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link as RouterLink } from 'react-router-dom'
import { ActionList, ActionMenu, Banner, Button, ConfirmationDialog, IconButton, Label, Stack, Text } from '@primer/react'
import { Blankslate, DataTable, Table } from '@primer/react/experimental'
import {
  CheckCircleIcon,
  CircleSlashIcon,
  DiffAddedIcon,
  KebabHorizontalIcon,
  PencilIcon,
  PlusIcon,
  TrashIcon,
} from '@primer/octicons-react'

import client from '../../api/client'
import { KIND_BY_ID, KIND_ORDER, prettySource } from '../../lib/entryKinds'
import type { OrgGroupEntry, OrgGroupEntryList } from '../../types'

export function EntriesTab({
  groupId,
  isAdmin,
  active,
  isDefault,
}: {
  groupId: string
  isAdmin: boolean
  active: boolean
  /** The standard set: shown, never changed. */
  isDefault: boolean
}) {
  const entriesQuery = useQuery({
    queryKey: ['group-entries', groupId],
    queryFn: () => client.get<OrgGroupEntryList>(`/portal/groups/${groupId}/entries`).then((r) => r.data),
    enabled: active,
  })

  const queryClient = useQueryClient()
  const [confirmDelete, setConfirmDelete] = useState<OrgGroupEntry | null>(null)

  const deleteMutation = useMutation({
    mutationFn: (entryId: string) => client.delete(`/portal/groups/${groupId}/entries/${entryId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['group-entries', groupId] }),
  })

  const toggleMutation = useMutation({
    mutationFn: ({ entryId, enabled }: { entryId: string; enabled: boolean }) =>
      client.patch(`/portal/groups/${groupId}/entries/${entryId}`, { enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['group-entries', groupId] }),
  })

  const entries: OrgGroupEntry[] = entriesQuery.data?.entries ?? []
  const canEdit = isAdmin && !isDefault

  if (!active) return null

  return (
    <Stack direction="vertical" gap="normal" padding="normal">
      {entriesQuery.isError && <Banner variant="critical" title="Failed to load entries" />}

      {isDefault && (
        <Banner
          variant="info"
          title="This is what igni ships"
          description="Read-only, and the group every other one starts from. Create a group from it to make your own version."
        />
      )}

      {entries.length === 0 ? (
        <Blankslate spacious>
          <Blankslate.Heading>Nothing configured yet</Blankslate.Heading>
          <Blankslate.Description>
            This group gives its members nothing yet. Add agents, MCP servers, plugins or settings —
            what is here is exactly what they get.
          </Blankslate.Description>
          {canEdit && (
            <Blankslate.PrimaryAction href={`/groups/${groupId}/entries/new`}>
              <PlusIcon /> Create first entry
            </Blankslate.PrimaryAction>
          )}
        </Blankslate>
      ) : (
        /* One table per kind, headed by it.
         *
         * A single table with a Kind column made you read every row to
         * answer "which of these are MCP servers?", and on a group that
         * is all agents it printed "Agents" nineteen times — a column
         * carrying no information. A heading answers the question
         * without reading a row, and it lets each kind show the columns
         * that mean something for it: a model only applies to an agent,
         * a source only to a plugin.
         */
        <Stack direction="vertical" gap="normal">
          <Stack direction="horizontal" justify="space-between" align="center">
            <Text size="small" className="group-detail-muted">
              Exactly what this group&apos;s members get — nothing else is shipped to them.
            </Text>
            {canEdit && (
              <Button as={RouterLink} to={`/groups/${groupId}/entries/new`} variant="primary" size="small">
                <PlusIcon /> New entry
              </Button>
            )}
          </Stack>

          {KIND_ORDER.map((kind) => {
            const rows = entries.filter((e) => e.kind === kind)
            if (rows.length === 0) return null
            const KindIcon = KIND_BY_ID[kind]?.icon ?? DiffAddedIcon
            return (
              <Table.Container key={kind} className="group-entries-table">
                <Table.Title id={`group-entries-${kind}`}>
                  {KIND_BY_ID[kind]?.label ?? kind} <Label variant="secondary">{rows.length}</Label>
                </Table.Title>
                <DataTable
                  aria-labelledby={`group-entries-${kind}`}
                  data={rows}
                  columns={[
                    {
                      header: 'Name',
                      field: 'entry_name' as const,
                      rowHeader: true,
                      renderCell: (row: OrgGroupEntry) => (
                        <Stack direction="horizontal" gap="condensed" align="center">
                          <KindIcon className="group-detail-muted" />
                          <RouterLink to={`/groups/${groupId}/entries/${row.id}/edit`}>
                            {row.entry_name}
                          </RouterLink>
                          {!row.enabled && <Label variant="secondary">Disabled</Label>}
                        </Stack>
                      ),
                    },
                    // A model is an agent's, and a source is a plugin's.
                    // Showing both columns everywhere would be four
                    // tables of em dashes.
                    ...(kind === 'agents'
                      ? [
                          {
                            header: 'Model',
                            id: 'model',
                            field: 'id' as const,
                            width: 200,
                            renderCell: (row: OrgGroupEntry) =>
                              row.model ? (
                                <Label variant="accent">{row.model}</Label>
                              ) : (
                                <Text size="small" className="group-detail-muted">
                                  Group default
                                </Text>
                              ),
                          },
                        ]
                      : []),
                    ...(kind === 'plugins'
                      ? [
                          {
                            header: 'Source',
                            id: 'source',
                            field: 'id' as const,
                            width: 200,
                            renderCell: (row: OrgGroupEntry) => (
                              <Text size="small" className="group-detail-muted">
                                {row.source_url ? prettySource(row.source_url) : 'Inline'}
                              </Text>
                            ),
                          },
                        ]
                      : []),
                    {
                      header: 'Updated',
                      field: 'updated_at' as const,
                      width: 120,
                      renderCell: (row: OrgGroupEntry) => (
                        <Text size="small" className="group-detail-muted">
                          {new Date(row.updated_at).toLocaleDateString()}
                        </Text>
                      ),
                    },
                    ...(canEdit
                      ? [
                          {
                            id: 'actions',
                            header: '',
                            field: 'id' as const,
                            width: 'auto' as const,
                            renderCell: (row: OrgGroupEntry) => (
                              <ActionMenu>
                                <ActionMenu.Anchor>
                                  <IconButton
                                    icon={KebabHorizontalIcon}
                                    aria-label={`Actions for ${row.entry_name}`}
                                    variant="invisible"
                                    size="small"
                                  />
                                </ActionMenu.Anchor>
                                <ActionMenu.Overlay>
                                  <ActionList>
                                    <ActionList.LinkItem
                                      href={`/groups/${groupId}/entries/${row.id}/edit`}
                                    >
                                      <ActionList.LeadingVisual>
                                        <PencilIcon />
                                      </ActionList.LeadingVisual>
                                      Edit
                                    </ActionList.LinkItem>
                                    <ActionList.Item
                                      onSelect={() =>
                                        toggleMutation.mutate({
                                          entryId: row.id,
                                          enabled: !row.enabled,
                                        })
                                      }
                                    >
                                      <ActionList.LeadingVisual>
                                        {row.enabled ? <CircleSlashIcon /> : <CheckCircleIcon />}
                                      </ActionList.LeadingVisual>
                                      {row.enabled ? 'Disable' : 'Enable'}
                                    </ActionList.Item>
                                    <ActionList.Divider />
                                    <ActionList.Item
                                      variant="danger"
                                      onSelect={() => setConfirmDelete(row)}
                                    >
                                      <ActionList.LeadingVisual>
                                        <TrashIcon />
                                      </ActionList.LeadingVisual>
                                      Delete
                                    </ActionList.Item>
                                  </ActionList>
                                </ActionMenu.Overlay>
                              </ActionMenu>
                            ),
                          },
                        ]
                      : []),
                  ]}
                />
              </Table.Container>
            )
          })}
        </Stack>
      )}

      {/* Deleting an entry takes something away from everyone in the
          group on their next sync, which is worth a question first. */}
      {confirmDelete && (
        <ConfirmationDialog
          title="Delete entry"
          confirmButtonContent="Delete"
          cancelButtonContent="Cancel"
          confirmButtonType="danger"
          onClose={(gesture) => {
            if (gesture === 'confirm') deleteMutation.mutate(confirmDelete.id)
            setConfirmDelete(null)
          }}
        >
          <Text>
            Delete <Text weight="semibold">{confirmDelete.entry_name}</Text>? Everyone in this group
            loses it on their next sync.
          </Text>
        </ConfirmationDialog>
      )}

    </Stack>
  )
}
