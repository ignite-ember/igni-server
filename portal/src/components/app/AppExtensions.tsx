import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Stack, Text, ToggleSwitch } from '@primer/react'
import { DataTable, Table } from '@primer/react/experimental'

import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import type { GroupExtensionList } from '../../types'
import type { OnFeedback } from './feedback'

/** The app's own extension switches: off until switched on here, whatever any group says. */
export function AppExtensions({ appId, onFeedback }: { appId: string; onFeedback: OnFeedback }) {
  const queryClient = useQueryClient()
  const extensions = useQuery({
    queryKey: ['app-extensions', appId],
    queryFn: () => client.get<GroupExtensionList>(`/portal/apps/${appId}/extensions`).then((r) => r.data),
  })
  const setEnabled = useMutation({
    mutationFn: ({ extensionId, enabled }: { extensionId: string; enabled: boolean }) =>
      client.put(`/portal/apps/${appId}/extensions/${extensionId}`, { enabled }),
    onSuccess: (_data, { enabled }) => {
      queryClient.invalidateQueries({ queryKey: ['app-extensions', appId] })
      onFeedback('success', enabled ? 'Extension switched on for this app' : 'Extension switched off for this app')
    },
    onError: (error) => onFeedback('critical', refusalOf(error)),
  })

  const list = extensions.data?.extensions ?? []

  return (
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
  )
}
