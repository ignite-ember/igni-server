import { FormControl, Stack, Textarea, TextInput } from '@primer/react'

import type { AuthMethodInfo, BackendKind } from '../../types'
import { isVertex } from './auth'
import { AuthFields } from './AuthFields'
import type { NodeDraft } from './nodeDraft'

/**
 * Where a cloud backend is, what it serves, and how igni server signs in
 * to it. Nothing to probe first: a cloud's models are typed, by the name
 * callers use, and mapped to the provider's own name where they differ.
 */
export function CloudBackendFields({
  draft,
  methods,
  onChange,
}: {
  draft: NodeDraft
  methods: AuthMethodInfo[]
  onChange: (change: Partial<NodeDraft>) => void
}) {
  return (
    <Stack gap="normal">
      <AddressFields draft={draft} onChange={onChange} />

      <FormControl required>
        <FormControl.Label>Models</FormControl.Label>
        <TextInput
          value={draft.modelsText}
          placeholder={
            draft.kind.endsWith('anthropic') || draft.kind === 'bedrock'
              ? 'claude-opus-5-5'
              : isVertex(draft.kind)
                ? 'gemini-pro'
                : 'gpt-5'
          }
          onChange={(e) => onChange({ modelsText: e.target.value })}
          block
        />
        <FormControl.Caption>Comma-separated: the names igni&apos;s users will ask for.</FormControl.Caption>
      </FormControl>

      <FormControl>
        <FormControl.Label>Provider&apos;s names</FormControl.Label>
        <Textarea
          value={draft.modelIdsText}
          placeholder={PROVIDER_NAME_EXAMPLE[draft.kind]}
          onChange={(e) => onChange({ modelIdsText: e.target.value })}
          rows={2}
          block
        />
        <FormControl.Caption>
          One <code>name = provider-name</code> per line, where the provider knows a model by another name —{' '}
          {PROVIDER_NAME_HINT[draft.kind]}.
        </FormControl.Caption>
      </FormControl>

      <AuthFields methods={methods} value={draft.auth} onChange={(auth) => onChange({ auth })} />
    </Stack>
  )
}

const PROVIDER_NAME_EXAMPLE: Record<BackendKind, string> = {
  openai: '',
  azure_openai: 'gpt-5 = my-gpt5-deployment',
  vertex_openai: 'gemini-pro = google/gemini-2.5-pro',
  anthropic: 'claude-opus = claude-opus-5-5',
  bedrock: 'claude-opus-5-5 = eu.anthropic.claude-opus-5-5-v1:0',
  vertex_anthropic: 'claude-opus-5-5 = claude-opus-5-5@20261101',
  azure_anthropic: 'claude-opus-5-5 = my-claude-deployment',
}

const PROVIDER_NAME_HINT: Record<BackendKind, string> = {
  openai: 'the model id',
  azure_openai: 'the Azure deployment',
  vertex_openai: 'the publisher/model id',
  anthropic: 'the model id, if your gateway names it differently',
  bedrock: 'the model ID or inference profile, which Bedrock needs for most Claude models',
  vertex_anthropic: 'the model version, such as claude-opus-5-5@20261101',
  azure_anthropic: 'the Foundry deployment',
}

/** Where a cloud backend is: a project and region, a region, or an endpoint. */
function AddressFields({ draft, onChange }: { draft: NodeDraft; onChange: (change: Partial<NodeDraft>) => void }) {
  const region = (
    <FormControl required>
      <FormControl.Label>Region</FormControl.Label>
      <TextInput
        value={draft.region}
        placeholder={draft.kind === 'bedrock' ? 'eu-central-1' : 'europe-west4'}
        onChange={(e) => onChange({ region: e.target.value })}
      />
      {isVertex(draft.kind) && <FormControl.Caption>Or global.</FormControl.Caption>}
    </FormControl>
  )
  if (isVertex(draft.kind)) {
    return (
      <Stack direction="horizontal" gap="normal">
        <FormControl required>
          <FormControl.Label>Project</FormControl.Label>
          <TextInput
            value={draft.project}
            placeholder="my-project"
            onChange={(e) => onChange({ project: e.target.value })}
          />
        </FormControl>
        {region}
      </Stack>
    )
  }
  if (draft.kind === 'bedrock') return region
  const anthropic = draft.kind === 'anthropic'
  return (
    <FormControl required={!anthropic}>
      <FormControl.Label>Endpoint</FormControl.Label>
      <TextInput
        value={draft.baseUrl}
        placeholder={
          anthropic
            ? 'https://api.anthropic.com'
            : draft.kind === 'azure_anthropic'
              ? 'https://my-resource.services.ai.azure.com/anthropic'
              : 'https://my-resource.openai.azure.com/openai/v1'
        }
        onChange={(e) => onChange({ baseUrl: e.target.value })}
        block
      />
      <FormControl.Caption>
        {anthropic
          ? "Leave blank for Anthropic's own API, or give your gateway's address."
          : draft.kind === 'azure_anthropic'
            ? "The Foundry resource's Anthropic endpoint."
            : "The resource's v1 endpoint, ending in /openai/v1."}
      </FormControl.Caption>
    </FormControl>
  )
}
