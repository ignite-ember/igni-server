import { ConfirmationDialog, Text } from '@primer/react'

import type { NodeStatus } from '../../types'
import { countOf } from '../../utils/plural'

/** Removing takes a node out of rotation for everyone, so it asks first. */
export function RemoveNodeDialog({ node, onClose }: { node: NodeStatus; onClose: (confirmed: boolean) => void }) {
  return (
    <ConfirmationDialog
      title={`Remove ${node.id}?`}
      confirmButtonContent="Remove node"
      cancelButtonContent="Cancel"
      confirmButtonType="danger"
      onClose={(gesture) => onClose(gesture === 'confirm')}
    >
      <Text size="medium">
        It stops receiving requests immediately.{' '}
        {node.in_flight > 0
          ? `${countOf(node.in_flight, 'request')} already in flight will finish.`
          : 'Nothing is in flight.'}{' '}
        The machine itself is untouched, and you can add it back with the same address.
      </Text>
    </ConfirmationDialog>
  )
}
