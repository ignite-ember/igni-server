import type { NodeProbeRequest, PinnedNodeInput } from '../../types'

/** The Add-a-node form, as one value. */
export interface NodeDraft {
  baseUrl: string
  apiKey: string
  nodeId: string
  models: string[]
  maxConcurrent: number
  multimodal: boolean
}

export const EMPTY_DRAFT: NodeDraft = {
  baseUrl: '',
  apiKey: '',
  nodeId: '',
  models: [],
  maxConcurrent: 64,
  multimodal: false,
}

export function probeRequestOf(draft: NodeDraft): NodeProbeRequest {
  return { base_url: draft.baseUrl, api_key: draft.apiKey || undefined }
}

export function pinnedNodeOf(draft: NodeDraft): PinnedNodeInput {
  return {
    node_id: draft.nodeId,
    base_url: draft.baseUrl,
    models: draft.models,
    api_key: draft.apiKey || undefined,
    max_concurrent: draft.maxConcurrent,
    multimodal: draft.multimodal,
  }
}

/** A name suggested from the address's first host label, or none for an unparseable URL. */
export function suggestedNodeId(baseUrl: string): string | null {
  try {
    return new URL(baseUrl).hostname.split('.')[0]
  } catch {
    return null
  }
}

/** `models` with `name` flipped, refusing to empty it: a node routed for no model does nothing. */
export function toggledModels(models: string[], name: string): string[] {
  const next = models.includes(name) ? models.filter((m) => m !== name) : [...models, name]
  return next.length === 0 ? models : next
}
