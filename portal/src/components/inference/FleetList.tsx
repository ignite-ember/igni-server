import { useState } from 'react'
import { Banner, Stack } from '@primer/react'
import { Blankslate } from '@primer/react/experimental'
import { ServerIcon } from '@primer/octicons-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import type { InferenceStatus, NodeStatus } from '../../types'
import { INFERENCE_STATUS_KEY, nodesOf } from './fleet'
import { NodeRow } from './NodeRow'
import { RemoveNodeDialog } from './RemoveNodeDialog'

/** The fleet: one row per GPU, whatever it serves. */
export function FleetList({ status, canManage }: { status: InferenceStatus; canManage: boolean }) {
  const queryClient = useQueryClient()
  const nodes = nodesOf(status)
  const [confirmRemove, setConfirmRemove] = useState<NodeStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const refresh = () => queryClient.invalidateQueries({ queryKey: INFERENCE_STATUS_KEY })

  const remove = useMutation({
    mutationFn: (nodeId: string) => client.delete(`/portal/inference/nodes/${nodeId}`),
    onMutate: () => setError(null),
    onSuccess: refresh,
    onError: (err) => setError(refusalOf(err, 'Could not remove the node.')),
  })

  // Draining records who and when; resuming clears that record.
  const setServing = useMutation({
    mutationFn: ({ id, serving }: { id: string; serving: boolean }) =>
      serving
        ? client.delete(`/portal/inference/nodes/${id}/drain`)
        : client.post(`/portal/inference/nodes/${id}/drain`, {}),
    onMutate: () => setError(null),
    onSuccess: refresh,
    onError: (err, { serving }) =>
      setError(refusalOf(err, serving ? 'Could not resume the node.' : 'Could not stop the node serving.')),
  })

  if (nodes.length === 0) return <NoNodes />

  return (
    <Stack gap="condensed">
      {error && <Banner variant="critical" title="That did not work" description={error} onDismiss={() => setError(null)} />}

      <div className="inference-nodes" aria-label="Inference nodes">
        {nodes.map((node) => (
          <NodeRow
            key={node.id}
            node={node}
            onRemove={() => setConfirmRemove(node)}
            onSetServing={(id, serving) => setServing.mutate({ id, serving })}
            // Holds the control until the refetch lands, so it does not spring back.
            busy={setServing.isPending && setServing.variables?.id === node.id}
            canManage={canManage}
          />
        ))}
      </div>

      {confirmRemove && (
        <RemoveNodeDialog
          node={confirmRemove}
          onClose={(confirmed) => {
            setConfirmRemove(null)
            if (confirmed) remove.mutate(confirmRemove.id)
          }}
        />
      )}
    </Stack>
  )
}

function NoNodes() {
  return (
    <Blankslate>
      <Blankslate.Visual>
        <ServerIcon size={20} />
      </Blankslate.Visual>
      <Blankslate.Heading>No inference nodes</Blankslate.Heading>
      <Blankslate.Description>
        Nothing serves this deployment, so chat and indexing will both fail. Add a node above, put one in{' '}
        <code>inference_backends</code>, or have one register itself.
      </Blankslate.Description>
    </Blankslate>
  )
}
