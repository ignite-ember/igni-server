import type {
  AuthMethodInfo,
  BackendKind,
  NodeProbeRequest,
  NodeProbeResult,
  PinnedNode,
  PinnedNodeInput,
  PinnedNodeUpdate,
} from '../../types'
import { type AuthDraft, authComplete, authFields } from './auth'
import { isCloud, KINDS } from './kinds'

/** The add and edit forms, as one value. */
export interface NodeDraft {
  kind: BackendKind
  nodeId: string
  baseUrl: string
  region: string
  project: string
  /** A GPU node's models, as its probe offered and the admin picked them. */
  models: string[]
  /** Models typed rather than probed: a cloud's, and every node's on the edit page. */
  modelsText: string
  /** `name = provider-name` lines: an Azure deployment, a Vertex model name. */
  modelIdsText: string
  maxConcurrent: number
  multimodal: boolean
  enabled: boolean
  /** A GPU node's key, which is its only secret. */
  apiKey: string
  auth: AuthDraft
}

export const EMPTY_DRAFT: NodeDraft = {
  kind: 'openai',
  nodeId: '',
  baseUrl: '',
  region: '',
  project: '',
  models: [],
  modelsText: '',
  modelIdsText: '',
  maxConcurrent: 64,
  multimodal: false,
  enabled: true,
  apiKey: '',
  auth: { method: 'none', settings: {}, secret: '' },
}

/** A stored node, as the edit form starts from it. Its secrets are never sent back, so none is filled in. */
export function draftOf(node: PinnedNode): NodeDraft {
  return {
    ...EMPTY_DRAFT,
    kind: node.kind,
    nodeId: node.node_id,
    baseUrl: node.base_url,
    region: node.region ?? '',
    project: node.project ?? '',
    models: node.models,
    modelsText: node.models.join(', '),
    modelIdsText: modelIdsText(node.model_ids),
    maxConcurrent: node.max_concurrent,
    multimodal: node.multimodal,
    enabled: node.enabled,
    auth: { method: node.auth, settings: node.auth_settings, secret: '' },
  }
}

/** Comma-separated model ids, trimmed, empties dropped. */
export function modelsIn(text: string): string[] {
  return text
    .split(',')
    .map((m) => m.trim())
    .filter(Boolean)
}

/** `name = provider-id` lines, one model each; a line without exactly one `=` maps nothing. */
export function parseModelIds(text: string): Record<string, string> {
  const pairs: Record<string, string> = {}
  for (const line of text.split('\n')) {
    const parts = line.split('=')
    if (parts.length === 2 && parts[0].trim() && parts[1].trim()) pairs[parts[0].trim()] = parts[1].trim()
  }
  return pairs
}

export function modelIdsText(modelIds: Record<string, string>): string {
  return Object.entries(modelIds)
    .map(([name, id]) => `${name} = ${id}`)
    .join('\n')
}

/** Where a backend is: a URL, or a cloud project and region the server builds the URL from. */
export function addressOf(draft: NodeDraft): Pick<PinnedNodeInput, 'base_url' | 'region' | 'project'> {
  switch (KINDS[draft.kind].address) {
    case 'project-region':
      return { region: draft.region.trim(), project: draft.project.trim() }
    case 'region':
      return { region: draft.region.trim() }
    case 'url-optional':
      // Blank means the provider's own address, which the server knows.
      return draft.baseUrl.trim() ? { base_url: draft.baseUrl.trim() } : {}
    case 'url':
      return { base_url: draft.baseUrl.trim() }
  }
}

/** Why the address cannot be saved, or null when it can. */
export function addressProblem(draft: NodeDraft): string | null {
  const url = draft.baseUrl.trim()
  const absolute = url.startsWith('http://') || url.startsWith('https://')
  switch (KINDS[draft.kind].address) {
    case 'project-region':
      return draft.project.trim() && draft.region.trim() ? null : 'A Vertex backend needs its project and region.'
    case 'region':
      return draft.region.trim() ? null : 'A Bedrock backend needs its region.'
    case 'url-optional':
      return !url || absolute ? null : 'The address has to start with http:// or https://'
    case 'url':
      return absolute ? null : 'The address has to start with http:// or https://'
  }
}

/**
 * Whether the add form can be sent. A GPU node needs its address checked and a
 * model picked from what it offered; a cloud backend needs its address, a model
 * and everything its auth method asks for — which cannot be known until the
 * server's table of methods has loaded.
 */
export function draftReady(draft: NodeDraft, method: AuthMethodInfo | undefined, probe: NodeProbeResult | null) {
  if (!draft.nodeId) return false
  if (!isCloud(draft.kind)) return probe !== null && draft.models.length > 0
  return modelsIn(draft.modelsText).length > 0 && addressProblem(draft) === null && authComplete(method, draft.auth)
}

export function probeRequestOf(draft: NodeDraft): NodeProbeRequest {
  return { base_url: draft.baseUrl, api_key: draft.apiKey || undefined }
}

/** The add form's request. A cloud backend's needs *method*, the chosen method's row from the server. */
export function pinnedNodeOf(draft: NodeDraft, method: AuthMethodInfo | undefined): PinnedNodeInput {
  if (!isCloud(draft.kind)) {
    return {
      node_id: draft.nodeId,
      base_url: draft.baseUrl,
      models: draft.models,
      api_key: draft.apiKey || undefined,
      max_concurrent: draft.maxConcurrent,
      multimodal: draft.multimodal,
    }
  }
  if (!method) throw new Error('A cloud backend cannot be sent before its auth methods have loaded')
  return {
    node_id: draft.nodeId,
    kind: draft.kind,
    models: modelsIn(draft.modelsText),
    model_ids: parseModelIds(draft.modelIdsText),
    max_concurrent: draft.maxConcurrent,
    multimodal: draft.multimodal,
    ...addressOf(draft),
    ...authFields(method, draft.auth),
  }
}

/**
 * The edit page's request: every field the page shows, so nothing is half-applied.
 * A secret goes only when one was typed — absent keeps the stored one.
 */
export function pinnedNodeUpdateOf(draft: NodeDraft, method: AuthMethodInfo | undefined): PinnedNodeUpdate {
  const update: PinnedNodeUpdate = {
    models: modelsIn(draft.modelsText),
    max_concurrent: draft.maxConcurrent,
    multimodal: draft.multimodal,
    enabled: draft.enabled,
    ...addressOf(draft),
  }
  if (!isCloud(draft.kind)) return draft.apiKey ? { ...update, api_key: draft.apiKey } : update
  if (!method) throw new Error('A cloud backend cannot be saved before its auth methods have loaded')
  return { ...update, model_ids: parseModelIds(draft.modelIdsText), ...authFields(method, draft.auth) }
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
