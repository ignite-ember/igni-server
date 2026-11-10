import { Stack } from '@primer/react'

import { StatCard } from '../StatCard'
import type { InferenceStatus } from '../../types'
import { percent } from './display'
import { nodesOf } from './fleet'

/** The numbers the page exists to deliver, in the portal's card idiom. */
export function FleetSummary({ status }: { status: InferenceStatus }) {
  const nodes = nodesOf(status)
  const unwell = nodes.filter((n) => n.health.state !== 'healthy')
  const slots = nodes.reduce((total, n) => total + n.max_concurrent, 0)

  return (
    <Stack direction="horizontal" gap="normal" wrap="wrap">
      <StatCard
        label="Nodes"
        value={String(nodes.length)}
        sub={nodes.length === 0 ? 'nothing configured yet' : `${slots} concurrent in total`}
      />
      <StatCard
        label="Capacity in use"
        value={status.total_capacity > 0 ? percent(status.utilization) : '—'}
        sub={
          status.total_capacity > 0 ? `${status.total_in_flight} of ${status.total_capacity} concurrent` : 'no nodes'
        }
      />
      <StatCard
        label="Needs attention"
        value={String(unwell.length)}
        sub={unwell.length ? unwell.map((n) => n.id).join(', ') : 'everything answering'}
        tone={unwell.length ? 'bad' : undefined}
      />
    </Stack>
  )
}
