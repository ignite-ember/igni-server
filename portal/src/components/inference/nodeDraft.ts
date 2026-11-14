import type { AuthMethodInfo, BackendKind, NodeProbeRequest, PinnedNodeInput } from '../../types'
import { type AuthDraft, authFields, isVertex, parseModelIds } from './auth'

/** The Add-a-node form, as one value. */
export interface NodeDraft {
  kind: BackendKind
  baseUrl: string
  apiKey: string
  nodeId: string
  models: string[]
  maxConcurrent: number
  multimodal: boolean
  /** A cloud backend's models, typed: there is no node to ask. */
  modelsText: string
  /** `name = provider-id` lines: an Azure deployment, a Vertex model name. */
  modelIdsText: string
  region: string
  project: string
  auth: AuthDraft
}

export const EMPTY_DRAFT: NodeDraft = {
  kind: 'openai',
  baseUrl: '',
  apiKey: '',
  nodeId: '',
  models: [],
  maxConcurrent: 64,
  multimodal: false,
  modelsText: '',
  modelIdsText: '',
  region: '',
  project: '',
  auth: { method: 'none', settings: {}, secret: '' },
}

export function probeRequestOf(draft: NodeDraft): NodeProbeRequest {
  return { base_url: draft.baseUrl, api_key: draft.apiKey || undefined }
}

/** Comma-separated model ids, trimmed, empties dropped. */
export function modelsIn(text: string): string[] {
  return text
    .split(',')
    .map((m) => m.trim())
    .filter(Boolean)
}

export function pinnedNodeOf(draft: NodeDraft, method?: AuthMethodInfo): PinnedNodeInput {
  if (draft.kind === 'openai') {
    return {
      node_id: draft.nodeId,
      base_url: draft.baseUrl,
      models: draft.models,
      api_key: draft.apiKey || undefined,
      max_concurrent: draft.maxConcurrent,
      multimodal: draft.multimodal,
    }
  }
  return {
    node_id: draft.nodeId,
    kind: draft.kind,
    models: modelsIn(draft.modelsText),
    model_ids: parseModelIds(draft.modelIdsText),
    max_concurrent: draft.maxConcurrent,
    multimodal: draft.multimodal,
    ...addressOf(draft),
    ...(method ? authFields(method, draft.auth) : {}),
  }
}

/**
 * Where a cloud backend is. Vertex is addressed by its project and region
 * and Bedrock by its region — the server builds the URL — and Anthropic's
 * own API needs no address unless a gateway stands in for it.
 */
export function addressOf(draft: NodeDraft): Partial<PinnedNodeInput> {
  if (isVertex(draft.kind)) return { region: draft.region.trim(), project: draft.project.trim() }
  if (draft.kind === 'bedrock') return { region: draft.region.trim() }
  if (draft.kind === 'anthropic') return draft.baseUrl.trim() ? { base_url: draft.baseUrl.trim() } : {}
  return { base_url: draft.baseUrl.trim() }
}

/** Whether a cloud backend's address is filled in. */
export function addressComplete(draft: NodeDraft): boolean {
  if (isVertex(draft.kind)) return Boolean(draft.project.trim() && draft.region.trim())
  if (draft.kind === 'bedrock') return Boolean(draft.region.trim())
  if (draft.kind === 'anthropic') return true
  return Boolean(draft.baseUrl.trim())
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
