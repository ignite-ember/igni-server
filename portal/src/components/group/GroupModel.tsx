import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ActionList, ActionMenu, Stack, Text } from '@primer/react'

import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import type { AvailableModels } from '../../types'
import type { ShowFeedback } from './feedback'

/**
 * Which model this group's people get.
 *
 * Usually a LoRA adapter: a legal team pinned to a legal-review adapter,
 * engineering to another, both served by the same GPUs. The list comes
 * from the deployment, because adapter names are whatever somebody
 * passed to `--lora-modules` and typing them from memory is how a group
 * ends up pointed at a model nothing serves.
 *
 * Only what a caller does *not* specify is affected. An explicit request
 * still wins, so this cannot silently entry somebody who asked for
 * something.
 */
export function GroupModel({
  groupId,
  current,
  isAdmin,
  onFeedback,
}: {
  groupId: string
  current: string | null
  isAdmin: boolean
  onFeedback: ShowFeedback
}) {
  const queryClient = useQueryClient()

  // The portal endpoint, not /chat/models. That one authenticates a real
  // user JWT; the portal session is a different scheme, so it answers
  // 401 — and the client treats any 401 as an expired session and logs
  // the admin out. Opening this page used to do exactly that.
  const modelsQuery = useQuery({
    queryKey: ['portal-inference-models'],
    queryFn: () => client.get<AvailableModels>('/portal/inference/models').then((r) => r.data),
    enabled: isAdmin,
  })

  const setModel = useMutation({
    mutationFn: (model: string | null) =>
      // An empty string clears it: null already means "not supplied" on
      // a partial update.
      client.patch(`/portal/groups/${groupId}`, { default_model: model ?? '' }).then((r) => r.data),
    onSuccess: (_data, model) => {
      queryClient.invalidateQueries({ queryKey: ['group', groupId] })
      onFeedback('success', model ? `Group routed to ${model}` : 'Group uses the deployment default')
    },
    onError: (err: unknown) => onFeedback('critical', refusalOf(err, 'Failed to set the model')),
  })

  const models = modelsQuery.data?.models ?? []

  // No label of its own: this renders inside a card already headed
  // "Model", and saying it twice is how a settings page starts looking
  // like a form nobody laid out.
  if (!isAdmin) {
    return (
      <Text size="medium">{current ?? 'Deployment default'}</Text>
    )
  }

  return (
    <Stack direction="horizontal" gap="condensed" align="center">
      <ActionMenu>
        <ActionMenu.Button size="small" loading={setModel.isPending}>
          {current ?? 'Deployment default'}
        </ActionMenu.Button>
        <ActionMenu.Overlay>
          <ActionList selectionVariant="single">
            <ActionList.Item selected={!current} onSelect={() => setModel.mutate(null)}>
              Deployment default
              <ActionList.Description variant="block">
                Whatever the node chosen for each request serves first.
              </ActionList.Description>
            </ActionList.Item>
            {models.length > 0 && <ActionList.Divider />}
            {models.map((model) => (
              <ActionList.Item key={model} selected={current === model} onSelect={() => setModel.mutate(model)}>
                {model}
              </ActionList.Item>
            ))}
          </ActionList>
        </ActionMenu.Overlay>
      </ActionMenu>
    </Stack>
  )
}
