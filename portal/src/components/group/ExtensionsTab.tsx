import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Stack, Text, ToggleSwitch } from '@primer/react'
import { Blankslate, DataTable, SkeletonText, Table } from '@primer/react/experimental'

import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import type { GroupExtensionList } from '../../types'
import type { ShowFeedback } from './feedback'

/**
 * Which approved extensions this group's people get.
 *
 * A super admin approving a service is trust; whether a group uses its
 * extension is the org admin's, and it is off until they switch it on.
 * The Default group can have extensions too: it is read-only as the copy
 * of what igni ships, but this is not part of that copy, and most people
 * are in it.
 */
export function ExtensionsTab({
  groupId,
  isAdmin,
  active,
  onFeedback,
}: {
  groupId: string
  isAdmin: boolean
  active: boolean
  onFeedback: ShowFeedback
}) {
  const queryClient = useQueryClient()

  const extensionsQuery = useQuery({
    queryKey: ['group-extensions', groupId],
    queryFn: () => client.get<GroupExtensionList>(`/portal/groups/${groupId}/extensions`).then((r) => r.data),
    enabled: active && isAdmin,
  })

  const setEnabled = useMutation({
    mutationFn: ({ extensionId, enabled }: { extensionId: string; enabled: boolean }) =>
      client.put(`/portal/groups/${groupId}/extensions/${extensionId}`, { enabled }),
    onSuccess: (_data, { enabled }) => {
      queryClient.invalidateQueries({ queryKey: ['group-extensions', groupId] })
      onFeedback('success', enabled ? 'Extension switched on for this group' : 'Extension switched off for this group')
    },
    onError: (err: unknown) => onFeedback('critical', refusalOf(err, 'Failed to change the extension')),
  })

  if (!active) return null

  if (!isAdmin) {
    return <Text size="medium">Only an organisation admin decides which extensions a group gets.</Text>
  }

  if (extensionsQuery.isLoading) {
    return <SkeletonText size="bodyMedium" maxWidth={300} />
  }

  const extensions = extensionsQuery.data?.extensions ?? []

  if (extensions.length === 0) {
    return (
      <Blankslate>
        <Blankslate.Heading>No extensions to give</Blankslate.Heading>
        <Blankslate.Description>
          An extension appears here once a super admin approves the service that ships it.
        </Blankslate.Description>
      </Blankslate>
    )
  }

  return (
    <Stack gap="normal">
      <Text size="medium">
        An approved extension reaches nobody until you switch it on for a group. People in this group get the ones
        switched on here.
      </Text>
      <Table.Container>
        <DataTable
          aria-label="Extensions"
          data={extensions.map((extension) => ({ id: extension.extension_id, ...extension }))}
          columns={[
            {
              header: 'Extension',
              field: 'name',
              rowHeader: true,
              renderCell: (row) => (
                <Stack gap="none">
                  <Text weight="semibold" id={`group-extension-${row.extension_id}`}>
                    {row.name}
                  </Text>
                  {row.description && <Text size="small">{row.description}</Text>}
                </Stack>
              ),
            },
            {
              header: 'Service',
              field: 'service_url',
              renderCell: (row) => (
                <a href={row.service_url} target="_blank" rel="noopener noreferrer">
                  {row.service_url}
                </a>
              ),
            },
            {
              header: 'This group',
              field: 'enabled',
              renderCell: (row) => (
                <ToggleSwitch
                  size="small"
                  aria-labelledby={`group-extension-${row.extension_id}`}
                  checked={row.enabled}
                  loading={setEnabled.isPending && setEnabled.variables?.extensionId === row.extension_id}
                  onClick={() => setEnabled.mutate({ extensionId: row.extension_id, enabled: !row.enabled })}
                />
              ),
            },
          ]}
        />
      </Table.Container>
    </Stack>
  )
}
