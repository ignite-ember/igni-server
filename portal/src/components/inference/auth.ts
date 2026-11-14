import { useQuery } from '@tanstack/react-query'

import client from '../../api/client'
import type {
  AuthMethod,
  AuthMethodInfo,
  AuthSetting,
  BackendKind,
  BackendKindInfo,
  PinnedNodeInput,
} from '../../types'

/** What the form says about one auth method: its name, the sentence under it, and its secret's field. */
export interface MethodForm {
  label: string
  caption: string
  /** What its secret is called, when it has one. */
  secretLabel: string
  /** A secret that is a document (a JSON key) rather than one line. */
  secretIsMultiline: boolean
}

/** One row per method: the single place the portal's knowledge of a method lives. */
export const METHODS: Record<AuthMethod, MethodForm> = {
  none: {
    label: 'None',
    caption: 'The node takes requests without a token, as a private-network vLLM usually does.',
    secretLabel: '',
    secretIsMultiline: false,
  },
  api_key: {
    label: 'API key',
    caption: 'A static key, stored encrypted and never shown again.',
    secretLabel: 'API key',
    secretIsMultiline: false,
  },
  ambient: {
    label: "The server's own identity",
    caption:
      'Nothing to enter: the identity igni server runs as — Workload Identity or its service account on Google ' +
      'Cloud, its system-assigned managed identity on Azure. Grant that identity access to the model.',
    secretLabel: '',
    secretIsMultiline: false,
  },
  managed_identity: {
    label: 'User-assigned managed identity',
    caption: 'A managed identity attached to the machines igni server runs on, chosen by its client ID.',
    secretLabel: '',
    secretIsMultiline: false,
  },
  client_secret: {
    label: 'App registration (client secret)',
    caption: "For a server outside Azure: an Entra app registration's tenant, client ID and secret.",
    secretLabel: 'Client secret',
    secretIsMultiline: false,
  },
  impersonate: {
    label: 'Impersonate a service account',
    caption:
      "The server's own identity acts as this service account. Grant the server Service Account Token Creator on it.",
    secretLabel: '',
    secretIsMultiline: false,
  },
  service_account_key: {
    label: 'Service-account key',
    caption: "For a server outside Google Cloud: a service account's JSON key, stored encrypted.",
    secretLabel: 'Service-account key (JSON)',
    secretIsMultiline: true,
  },
}

/** Each setting's label and an example of its value. */
export const SETTINGS: Record<AuthSetting, { label: string; placeholder: string }> = {
  client_id: { label: 'Client ID', placeholder: '00000000-0000-0000-0000-000000000000' },
  tenant_id: { label: 'Tenant ID', placeholder: '00000000-0000-0000-0000-000000000000' },
  service_account: { label: 'Service account', placeholder: 'igni-server@my-project.iam.gserviceaccount.com' },
}

/** What a secret field says when one is already stored and need not be typed again. */
export const SECRET_STORED = 'stored — leave blank to keep it'

/** The form's auth section, as one value. */
export interface AuthDraft {
  method: AuthMethod
  settings: Partial<Record<AuthSetting, string>>
  secret: string
}

/** What the auth section sends: the method, its settings, and its secret when one was typed. */
export type AuthFields = Pick<PinnedNodeInput, 'auth' | 'auth_settings' | 'api_key' | 'credential'>

export const AUTH_METHODS_KEY = ['inference-auth-methods']

/** Every kind and its methods, from the server's own table. */
export function useAuthMethods() {
  return useQuery<BackendKindInfo[]>({
    queryKey: AUTH_METHODS_KEY,
    queryFn: () => client.get<BackendKindInfo[]>('/portal/inference/auth-methods').then((r) => r.data),
    staleTime: Infinity,
  })
}

/** The methods *kind* offers, or none while the table loads. */
export function methodsOf(table: BackendKindInfo[] | undefined, kind: BackendKind): AuthMethodInfo[] {
  return table?.find((row) => row.kind === kind)?.methods ?? []
}

/** A draft for the first of *methods* — the kind's default — with nothing typed. */
export function defaultAuth(methods: AuthMethodInfo[]): AuthDraft {
  return { method: methods[0]?.method ?? 'none', settings: {}, secret: '' }
}

/** Whether every setting the method needs is filled, and its secret given or already stored. */
export function authComplete(info: AuthMethodInfo | undefined, draft: AuthDraft, secretStored = false): boolean {
  if (!info) return false
  const settings = info.settings.every((name) => (draft.settings[name] ?? '').trim())
  return settings && (!info.secret || Boolean(draft.secret) || secretStored)
}

/**
 * The request fields for the auth section.
 *
 * Only the method's own settings are sent, and its secret only when one
 * was typed: the API treats an absent secret as "keep the stored one".
 */
export function authFields(info: AuthMethodInfo, draft: AuthDraft): AuthFields {
  const auth_settings: Partial<Record<AuthSetting, string>> = {}
  for (const name of info.settings) auth_settings[name] = (draft.settings[name] ?? '').trim()
  const fields: AuthFields = { auth: draft.method, auth_settings }
  if (info.secret === 'api_key' && draft.secret) fields.api_key = draft.secret
  if (info.secret === 'credential' && draft.secret) fields.credential = draft.secret
  return fields
}
