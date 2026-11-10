import { useState } from 'react'
import { Button, Details, Stack } from '@primer/react'
import { PencilIcon, TrashIcon } from '@primer/octicons-react'
import { Link as RouterLink } from 'react-router-dom'

import type { NodeStatus } from '../../types'
import { NodeFacts } from './NodeFacts'
import { NodeGauges } from './NodeGauges'
import { NodeSummary } from './NodeSummary'

/**
 * One node: a native disclosure whose detail is rendered only while open,
 * so a mostly collapsed fleet does not re-render every gauge on each poll.
 */
export function NodeRow({
  node,
  onRemove,
  onSetServing,
  busy,
  canManage,
}: {
  node: NodeStatus
  onRemove: (id: string) => void
  onSetServing: (id: string, serving: boolean) => void
  busy: boolean
  /** A super admin: the fleet serves every organisation (SER-32). */
  canManage: boolean
}) {
  const [open, setOpen] = useState(false)

  return (
    <Details
      className="inference-node"
      open={open}
      onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}
    >
      <NodeSummary node={node} />

      {open && (
        <div className="inference-node-detail">
          <NodeGauges node={node} />
          <NodeFacts node={node} canManage={canManage} busy={busy} onSetServing={onSetServing} />
          {/* Configured and registered nodes change where they were defined. */}
          {canManage && node.source === 'pinned' && <PinnedNodeActions nodeId={node.id} onRemove={onRemove} />}
        </div>
      )}
    </Details>
  )
}

function PinnedNodeActions({ nodeId, onRemove }: { nodeId: string; onRemove: (id: string) => void }) {
  return (
    <Stack direction="horizontal" gap="condensed" padding="none">
      <Button size="small" leadingVisual={PencilIcon} as={RouterLink} to={`/inference/nodes/${nodeId}/edit`}>
        Edit
      </Button>
      <Button variant="danger" size="small" leadingVisual={TrashIcon} onClick={() => onRemove(nodeId)}>
        Remove node
      </Button>
    </Stack>
  )
}
