import { Button, Stack, Text } from '@primer/react'

import type { NodeStatus } from '../../types'
import { formatWhen } from './display'

/**
 * Stop or resume routing to a node, whatever its source: this changes
 * routing, not the node's definition. Two named actions rather than a
 * switch, because "Stop serving" cannot be read backwards.
 */
export function ServingControl({
  node,
  canManage,
  busy,
  onSetServing,
}: {
  node: NodeStatus
  canManage: boolean
  busy: boolean
  onSetServing: (id: string, serving: boolean) => void
}) {
  return (
    <Stack direction="horizontal" gap="condensed" align="center" padding="none">
      {canManage && (
        <Button
          size="small"
          variant={node.drain ? 'primary' : 'default'}
          disabled={busy}
          onClick={() => onSetServing(node.id, node.drain != null)}
        >
          {node.drain ? `Resume ${node.id}` : `Stop serving ${node.id}`}
        </Button>
      )}
      <Text size="small" className="inference-muted">
        {node.drain
          ? `stopped by ${node.drain.by_email ?? node.drain.by} ${formatWhen(node.drain.at)}${
              node.drain.reason ? ` — ${node.drain.reason}` : ''
            }`
          : 'in-flight requests finish; new ones stop immediately'}
      </Text>
    </Stack>
  )
}
