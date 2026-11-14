import { useQuery } from '@tanstack/react-query'

import client from '../../api/client'
import type { AuthMethod, AuthMethodInfo, BackendKind, BackendKindInfo } from '../../types'

/** What the form calls each kind. */
export const KIND_LABEL: Record<BackendKind, string> = {
  openai: 'GPU node or OpenAI-compatible API',
  azure_openai: 'Azure OpenAI',
  vertex_openai: 'Google Vertex AI',
}

/** What the form calls each method, and the sentence under it. */
export const METHOD_LABEL: Record<AuthMethod, { label: string; caption: string }> = {
  none: {
    label: 'None',
    caption: 'The node takes requests without a token, as a private-network vLLM usually does.',
  },
  api_key: {
    label: 'API key',
    caption: 'A static key, stored encrypted and never shown again.',
  },
  ambient: {
    label: "The server's own identity",
    caption:
      'Nothing to enter: the identity igni server runs as — Workload Identity or its service account on Google ' +
      'Cloud, its system-assigned managed identity on Azure. Grant that identity access to the model.',
  },
  managed_identity: {
    label: 'User-assigned managed identity',
    caption: 'A managed identity attached to the machines igni server runs on, chosen by its client ID.',
  },
  client_secret: {
    label: 'App registration (client secret)',
    caption: "For a server outside Azure: an Entra app registration's tenant, client ID and secret.",
  },
  impersonate: {
    label: 'Impersonate a service account',
    caption:
      "The server's own identity acts as this service account. Grant the server Service Account Token Creator on it.",
  },
  service_account_key: {
    label: 'Service-account key',
    caption: "For a server outside Google Cloud: a service account's JSON key, stored encrypted.",
  },
}

/** Each setting's label and an example of its value. */
export const SETTING_LABEL: Record<string, { label: string; placeholder: string }> = {
  client_id: {
    label: 'Client ID',
    placeholder: '00000000-0000-0000-0000-000000000000',
  },
  tenant_id: {
    label: 'Tenant ID',
    placeholder: '00000000-0000-0000-0000-000000000000',
  },
  service_account: {
    label: 'Service account',
    placeholder: 'igni-server@my-project.iam.gserviceaccount.com',
  },
}

/** What a method's secret is called. */
export function secretLabel(method: AuthMethod): string {
  if (method === 'client_secret') return 'Client secret'
  if (method === 'service_account_key') return 'Service-account key (JSON)'
  return 'API key'
}

/** The form's auth section, as one value. */
export interface AuthDraft {
  method: AuthMethod
  settings: Record<string, string>
  secret: string
}

export const AUTH_METHODS_KEY = ['inference-auth-methods']

/** Every kind and its methods, from the server's own table. */
export function useAuthMethods() {
  return useQuery<BackendKindInfo[]>({
    queryKey: AUTH_METHODS_KEY,
    queryFn: () => client.get('/portal/inference/auth-methods').then((r) => r.data),
    staleTime: Infinity,
  })
}

/** The methods *kind* offers, or none while the table loads. */
export function methodsOf(table: BackendKindInfo[] | undefined, kind: BackendKind): AuthMethodInfo[] {
  return table?.find((row) => row.kind === kind)?.methods ?? []
}

/** A draft for *kind*'s default method, with nothing typed. */
export function defaultAuth(methods: AuthMethodInfo[]): AuthDraft {
  return { method: methods[0]?.method ?? 'none', settings: {}, secret: '' }
}

/** Whether every setting the method needs is filled, and its secret given or already stored. */
export function authComplete(info: AuthMethodInfo | undefined, draft: AuthDraft, secretStored = false): boolean {
  if (!info) return false
  const settings = info.settings.every((name) => (draft.settings[name] ?? '').trim())
  const secret = !info.secret || Boolean(draft.secret) || secretStored
  return settings && secret
}

/**
 * The request fields for the auth section.
 *
 * Only the method's own settings are sent, and its secret only when one
 * was typed: the API treats an absent secret as "keep the stored one".
 */
export function authFields(info: AuthMethodInfo, draft: AuthDraft) {
  const auth_settings = Object.fromEntries(info.settings.map((name) => [name, (draft.settings[name] ?? '').trim()]))
  return {
    auth: draft.method,
    auth_settings,
    ...(info.secret && draft.secret ? { [info.secret]: draft.secret } : {}),
  }
}

/** `name = provider-id` lines, one model each; a line without `=` maps nothing. */
export function parseModelIds(text: string): Record<string, string> {
  const pairs = text
    .split('\n')
    .map((line) => line.split('='))
    .filter((parts) => parts.length === 2 && parts[0].trim() && parts[1].trim())
    .map(([name, id]) => [name.trim(), id.trim()])
  return Object.fromEntries(pairs)
}

export function modelIdsText(modelIds: Record<string, string>): string {
  return Object.entries(modelIds)
    .map(([name, id]) => `${name} = ${id}`)
    .join('\n')
}
