import { Button, FormControl, Stack, TextInput } from '@primer/react'
import { useMutation } from '@tanstack/react-query'

import client from '../../api/client'
import type { NodeProbeResult } from '../../types'
import { KINDS } from './kinds'
import { type NodeDraft, probeRequestOf, suggestedNodeId, toggledModels } from './nodeDraft'
import { ProbeOutcome } from './ProbeOutcome'

/**
 * A GPU node in two steps: say where it is, press Check, and pick from
 * the models actually there — a mistyped model id never errors, it just
 * never matches a request.
 */
export function GpuNodeFields({
  draft,
  probe,
  onChange,
  onProbe,
  onError,
}: {
  draft: NodeDraft
  probe: NodeProbeResult | null
  onChange: (change: Partial<NodeDraft>) => void
  onProbe: (probe: NodeProbeResult | null) => void
  onError: (error: string | null) => void
}) {
  const check = useMutation({
    mutationFn: () =>
      client.post<NodeProbeResult>('/portal/inference/nodes/discover', probeRequestOf(draft)).then((r) => r.data),
    onMutate: () => onError(null),
    onSuccess: (result) => {
      onProbe(result)
      onChange({ models: result.models, nodeId: draft.nodeId || (suggestedNodeId(draft.baseUrl) ?? '') })
    },
    onError: () => onError('Could not reach that address.'),
  })

  return (
    <>
      <FormControl required>
        <FormControl.Label>Address</FormControl.Label>
        <Stack direction="horizontal" gap="condensed" align="center">
          <TextInput
            value={draft.baseUrl}
            placeholder={KINDS.openai.endpointPlaceholder}
            onChange={(e) => {
              onChange({ baseUrl: e.target.value })
              onProbe(null)
            }}
            block
            className="inference-url-field"
          />
          <Button onClick={() => check.mutate()} loading={check.isPending} disabled={!draft.baseUrl || check.isPending}>
            Check
          </Button>
        </Stack>
        <FormControl.Caption>{KINDS.openai.endpointCaption}</FormControl.Caption>
      </FormControl>

      <FormControl>
        <FormControl.Label>Token</FormControl.Label>
        <TextInput type="password" value={draft.apiKey} onChange={(e) => onChange({ apiKey: e.target.value })} block />
        <FormControl.Caption>
          Only if the node sits behind an auth proxy. Most private-network vLLM nodes have none.
        </FormControl.Caption>
      </FormControl>

      {probe && (
        <ProbeOutcome
          probe={probe}
          models={draft.models}
          onToggleModel={(name) => onChange({ models: toggledModels(draft.models, name) })}
        />
      )}
    </>
  )
}
