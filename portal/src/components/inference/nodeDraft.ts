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

/** The provider names typed, and every non-blank line that is not `name = provider-id`. */
export interface ModelIds {
  pairs: Record<string, string>
  rejected: string[]
}

/** `name = provider-id` lines, one model each. A line that is not one is kept as rejected, never dropped. */
export function parseModelIds(text: string): ModelIds {
  const pairs: Record<string, string> = {}
  const rejected: string[] = []
  for (const raw of text.split('\n')) {
    const line = raw.trim()
    if (!line) continue
    const parts = line.split('=')
    if (parts.length === 2 && parts[0].trim() && parts[1].trim()) pairs[parts[0].trim()] = parts[1].trim()
    else rejected.push(line)
  }
  return { pairs, rejected }
}

export function modelIdsText(modelIds: Record<string, string>): string {
  return Object.entries(modelIds)
    .map(([name, id]) => `${name} = ${id}`)
    .join('\n')
}

/**
 * Where a backend is: a URL, or a cloud project and region the server builds the URL from. A
 * blank optional URL means the provider's own: left out when adding, sent empty when editing,
 * so that clearing a gateway goes back to the provider rather than keeping the old one.
 */
export function addressOf(draft: NodeDraft, adding: boolean): Pick<PinnedNodeInput, 'base_url' | 'region' | 'project'> {
  switch (KINDS[draft.kind].address) {
    case 'project-region':
      return { region: draft.region.trim(), project: draft.project.trim() }
    case 'region':
      return { region: draft.region.trim() }
    case 'url-optional':
      return draft.baseUrl.trim() || !adding ? { base_url: draft.baseUrl.trim() } : {}
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
      return draft.project.trim() && draft.region.trim()
        ? null
        : `${KINDS[draft.kind].label} needs its project and region.`
    case 'region':
      return draft.region.trim() ? null : `${KINDS[draft.kind].label} needs its region.`
    case 'url-optional':
      return !url || absolute ? null : 'The address has to start with http:// or https://'
    case 'url':
      return absolute ? null : 'The address has to start with http:// or https://'
  }
}

/** What the form checking a draft knows: whether it adds, the GPU node's probe, whether a secret is stored. */
export interface DraftCheck {
  adding: boolean
  probe?: NodeProbeResult | null
  secretStored?: boolean
}

/**
 * Why the draft cannot be sent yet, or null when it can — the one rule set the add
 * dialog and the edit page both say, before the API would. Adding a GPU node needs
 * its address checked and a model picked from what it offered; everything else
 * needs its address and models typed. A cloud backend needs whatever its auth
 * method asks for, which cannot be known until the server's table has loaded.
 */
export function draftProblem(draft: NodeDraft, method: AuthMethodInfo | undefined, check: DraftCheck): string | null {
  if (check.adding && !isCloud(draft.kind)) {
    if (!check.probe) return 'Check the node first, to see which models it serves.'
    if (draft.models.length === 0) return 'Pick at least one model to route here.'
  } else {
    const address = addressProblem(draft)
    if (address) return address
    if (modelsIn(draft.modelsText).length === 0) {
      return 'A node needs at least one model, or nothing will ever be routed to it.'
    }
  }
  const [rejected] = parseModelIds(draft.modelIdsText).rejected
  if (rejected !== undefined) return `Each provider name is a line "name = provider-name", and this is not: ${rejected}`
  if (!(draft.maxConcurrent > 0)) return 'Concurrency has to be at least 1.'
  if (isCloud(draft.kind) && !authComplete(method, draft.auth, check.secretStored ?? false)) {
    return 'The chosen authentication needs every field filled in.'
  }
  if (check.adding && !draft.nodeId.trim()) return 'Give the node a name.'
  return null
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
    model_ids: parseModelIds(draft.modelIdsText).pairs,
    max_concurrent: draft.maxConcurrent,
    multimodal: draft.multimodal,
    ...addressOf(draft, true),
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
    ...addressOf(draft, false),
  }
  if (!isCloud(draft.kind)) return draft.apiKey ? { ...update, api_key: draft.apiKey } : update
  if (!method) throw new Error('A cloud backend cannot be saved before its auth methods have loaded')
  return { ...update, model_ids: parseModelIds(draft.modelIdsText).pairs, ...authFields(method, draft.auth) }
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
