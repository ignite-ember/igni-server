import type { InferenceStatus, NodeStatus } from '../../types'

export const INFERENCE_STATUS_KEY = ['inference-status']

/** Every node, in a stable order. */
export function nodesOf(status: InferenceStatus): NodeStatus[] {
  return [...status.nodes].sort((a, b) => a.id.localeCompare(b.id))
}
