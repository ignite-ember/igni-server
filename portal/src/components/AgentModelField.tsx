import { useQuery } from '@tanstack/react-query'
import { ActionList, ActionMenu, FormControl, Stack, Text } from '@primer/react'

import client from '../api/client'

/**
 * Which model one agent runs against.
 *
 * The group already pins a model for everything its people send, which
 * answers "what does this team use" but not "what does *this agent*
 * use" — and a legal team plausibly wants contract review on one adapter
 * and case summaries on another, both served by the same GPUs.
 *
 * A list rather than a text field because adapter names are whatever
 * somebody passed to `--lora-modules`, and typing one from memory is how
 * an agent ends up pointed at a model nothing serves. The server refuses
 * those anyway; this is so the admin never gets that far.
 *
 * Empty means the group's default, then the deployment's.
 */
export function AgentModelField({
  value,
  onChange,
  groupDefault,
  disabled = false,
}: {
  value: string | null
  onChange: (model: string | null) => void
  /** The group's own default, shown as what "inherit" resolves to. */
  groupDefault?: string | null
  /** Read-only: the entry belongs to the group that holds what igni ships. */
  disabled?: boolean
}) {
  // The portal endpoint, not /chat/models: that one authenticates a real
  // user JWT and answers 401 to a portal session, which the API client
  // reads as an expired login.
  const modelsQuery = useQuery({
    queryKey: ['portal-inference-models'],
    queryFn: () => client.get<{ models: string[] }>('/portal/inference/models').then((r) => r.data),
    enabled: !disabled,
  })

  const models = modelsQuery.data?.models ?? []
  const inherited = groupDefault ? `Group default (${groupDefault})` : 'Group default'

  return (
    <FormControl>
      <FormControl.Label>Model</FormControl.Label>
      <Stack direction="horizontal" gap="condensed" align="center">
        {disabled ? (
          <Text size="small">{value ?? inherited}</Text>
        ) : (
        <ActionMenu>
          <ActionMenu.Button size="small">{value ?? inherited}</ActionMenu.Button>
          <ActionMenu.Overlay>
            <ActionList selectionVariant="single">
              <ActionList.Item selected={!value} onSelect={() => onChange(null)}>
                {inherited}
                <ActionList.Description variant="block">
                  Falls back to the deployment default if the group sets none.
                </ActionList.Description>
              </ActionList.Item>
              {models.length > 0 && <ActionList.Divider />}
              {models.map((model) => (
                <ActionList.Item key={model} selected={value === model} onSelect={() => onChange(model)}>
                  {model}
                </ActionList.Item>
              ))}
            </ActionList>
          </ActionMenu.Overlay>
        </ActionMenu>
        )}
        {!disabled && modelsQuery.isError && (
          <Text size="small" className="group-detail-muted">
            Could not reach the inference pool — the model list may be incomplete.
          </Text>
        )}
      </Stack>
    </FormControl>
  )
}
