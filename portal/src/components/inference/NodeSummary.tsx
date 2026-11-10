import { Details, Label, ProgressBar, Text } from '@primer/react'
import { ChevronRightIcon, DotFillIcon } from '@primer/octicons-react'

import type { NodeStatus } from '../../types'
import { countOf } from '../../utils/plural'
import { HEALTH_LABEL, HEALTH_TONE, loadColor, occupancy, percent } from './display'

/**
 * The collapsed row: only what you scan for — up or down, how loaded,
 * anything queued — in fixed column tracks so rows line up.
 */
export function NodeSummary({ node }: { node: NodeStatus }) {
  const ratio = occupancy(node.in_flight, node.max_concurrent)
  const label = HEALTH_LABEL[node.health.state]

  return (
    <Details.Summary className="inference-node-summary">
      <span className="inference-node-caret" aria-hidden="true">
        <ChevronRightIcon />
      </span>

      <DotFillIcon
        className={`inference-dot inference-dot--${HEALTH_TONE[node.health.state]}`}
        aria-label={node.health.detail ? `${label}: ${node.health.detail}` : label}
      />

      <Text size="small" weight="semibold" className="inference-node-name">
        {node.id}
      </Text>

      {/* Reserves its width when empty, so queueing cannot shift the bar. */}
      <span className="inference-node-queue">
        {node.drain ? (
          <Label variant="secondary">not serving</Label>
        ) : node.metrics?.waiting ? (
          <Label variant="attention">{node.metrics.waiting} queued</Label>
        ) : null}
      </span>

      <span className="inference-node-load">
        <ProgressBar
          progress={Math.min(100, ratio * 100)}
          bg={loadColor(ratio)}
          barSize="small"
          inline
          aria-label={`${node.in_flight} of ${node.max_concurrent} in flight`}
        />
        <Text size="small" className="inference-numeric inference-node-percent">
          {percent(ratio)}
        </Text>
      </span>

      <Text size="small" className="inference-muted inference-node-models" title={node.models.join(', ')}>
        {node.models.length === 1 ? node.models[0] : countOf(node.models.length, 'model')}
      </Text>
    </Details.Summary>
  )
}
