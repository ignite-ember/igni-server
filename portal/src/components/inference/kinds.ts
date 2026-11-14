import type { BackendKind } from '../../types'

/** How a kind is addressed: a URL the admin pastes, or a cloud project and region the server builds one from. */
export type AddressShape = 'url' | 'project-region'

/** Everything the portal says and asks about one kind of backend, in one row. */
export interface KindForm {
  label: string
  /** A provider's service rather than the customer's own GPU node. */
  cloud: boolean
  address: AddressShape
  endpointPlaceholder: string
  endpointCaption: string
  regionPlaceholder: string
  modelPlaceholder: string
  /** A `name = provider-name` line, and what the provider's name is. */
  providerNameExample: string
  providerNameHint: string
}

/** One row per kind: the single place the portal's knowledge of a kind lives. */
export const KINDS: Record<BackendKind, KindForm> = {
  openai: {
    label: 'GPU node or OpenAI-compatible API',
    cloud: false,
    address: 'url',
    endpointPlaceholder: 'http://gpu-rack-01.internal:8000/v1',
    endpointCaption: 'The OpenAI-compatible endpoint. We ask it which models it serves.',
    regionPlaceholder: '',
    modelPlaceholder: 'igni-1.0-27b',
    providerNameExample: '',
    providerNameHint: 'the model id',
  },
  azure_openai: {
    label: 'Azure OpenAI',
    cloud: true,
    address: 'url',
    endpointPlaceholder: 'https://my-resource.openai.azure.com/openai/v1',
    endpointCaption: "The resource's v1 endpoint, ending in /openai/v1.",
    regionPlaceholder: '',
    modelPlaceholder: 'gpt-5',
    providerNameExample: 'gpt-5 = my-gpt5-deployment',
    providerNameHint: 'the Azure deployment',
  },
  vertex_openai: {
    label: 'Google Vertex AI',
    cloud: true,
    address: 'project-region',
    endpointPlaceholder: '',
    endpointCaption: '',
    regionPlaceholder: 'europe-west4',
    modelPlaceholder: 'gemini-pro',
    providerNameExample: 'gemini-pro = google/gemini-2.5-pro',
    providerNameHint: 'the publisher/model id',
  },
}

/** Whether *kind* is a cloud's service the server signs for, rather than the customer's own node. */
export function isCloud(kind: BackendKind): boolean {
  return KINDS[kind].cloud
}
