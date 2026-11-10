import { Banner, Checkbox, FormControl, Stack, Text } from '@primer/react'

import type { NodeProbeResult } from '../../types'
import { countOf } from '../../utils/plural'

/** What Check found at the address, and the models it offers to route. */
export function ProbeOutcome({
  probe,
  models,
  onToggleModel,
}: {
  probe: NodeProbeResult
  models: string[]
  onToggleModel: (name: string) => void
}) {
  if (probe.needs_credentials) {
    return (
      <Banner
        variant="warning"
        title="That node wants a token"
        description={
          <Text size="small">
            It answered but refused the request. A vLLM node started with <code>--api-key</code> protects{' '}
            <code>/v1</code>, which is what we ask. Put the token above and press Check again.
          </Text>
        }
      />
    )
  }

  if (!probe.reachable) {
    return (
      <Banner
        variant="warning"
        title="That address did not answer"
        description={
          <Text size="small">
            {probe.detail ?? 'No response.'} You can still add it — it will show as unreachable until it comes up.
          </Text>
        }
      />
    )
  }

  return (
    <>
      <Banner
        variant="success"
        title={`Reachable — ${countOf(probe.models.length, 'model')}`}
        description={
          <Text size="small">
            Pick which to route here. On a node started with <code>--enable-lora</code> the adapters appear
            alongside the base model.
          </Text>
        }
      />

      <Stack gap="condensed">
        {probe.models.map((name) => (
          <FormControl key={name}>
            <Checkbox checked={models.includes(name)} onChange={() => onToggleModel(name)} />
            <FormControl.Label>{name}</FormControl.Label>
          </FormControl>
        ))}
        {probe.models.length === 0 && (
          <Text size="small" className="inference-muted">
            It answered but listed no models, so there is nothing to route here yet.
          </Text>
        )}
      </Stack>
    </>
  )
}
