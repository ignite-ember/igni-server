import { FormControl, Stack, Textarea, TextInput } from '@primer/react'

import type { AuthMethodInfo } from '../../types'
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
      {draft.kind === 'vertex_openai' ? (
        <Stack direction="horizontal" gap="normal">
          <FormControl required>
            <FormControl.Label>Project</FormControl.Label>
            <TextInput
              value={draft.project}
              placeholder="my-project"
              onChange={(e) => onChange({ project: e.target.value })}
            />
          </FormControl>
          <FormControl required>
            <FormControl.Label>Region</FormControl.Label>
            <TextInput
              value={draft.region}
              placeholder="europe-west4"
              onChange={(e) => onChange({ region: e.target.value })}
            />
            <FormControl.Caption>Or global.</FormControl.Caption>
          </FormControl>
        </Stack>
      ) : (
        <FormControl required>
          <FormControl.Label>Endpoint</FormControl.Label>
          <TextInput
            value={draft.baseUrl}
            placeholder="https://my-resource.openai.azure.com/openai/v1"
            onChange={(e) => onChange({ baseUrl: e.target.value })}
            block
          />
          <FormControl.Caption>The resource&apos;s v1 endpoint, ending in /openai/v1.</FormControl.Caption>
        </FormControl>
      )}

      <FormControl required>
        <FormControl.Label>Models</FormControl.Label>
        <TextInput
          value={draft.modelsText}
          placeholder={draft.kind === 'vertex_openai' ? 'gemini-pro' : 'gpt-5'}
          onChange={(e) => onChange({ modelsText: e.target.value })}
          block
        />
        <FormControl.Caption>Comma-separated: the names igni&apos;s users will ask for.</FormControl.Caption>
      </FormControl>

      <FormControl>
        <FormControl.Label>Provider&apos;s names</FormControl.Label>
        <Textarea
          value={draft.modelIdsText}
          placeholder={
            draft.kind === 'vertex_openai' ? 'gemini-pro = google/gemini-2.5-pro' : 'gpt-5 = my-gpt5-deployment'
          }
          onChange={(e) => onChange({ modelIdsText: e.target.value })}
          rows={2}
          block
        />
        <FormControl.Caption>
          One <code>name = provider-name</code> per line, where the provider knows a model by another name —{' '}
          {draft.kind === 'vertex_openai' ? 'the publisher/model id' : 'the Azure deployment'}.
        </FormControl.Caption>
      </FormControl>

      <AuthFields methods={methods} value={draft.auth} onChange={(auth) => onChange({ auth })} />
    </Stack>
  )
}
