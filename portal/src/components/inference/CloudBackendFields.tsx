import { FormControl, Stack, Textarea, TextInput } from '@primer/react'

import type { AuthMethodInfo } from '../../types'
import { AuthFields } from './AuthFields'
import { KINDS } from './kinds'
import type { NodeDraft } from './nodeDraft'

/**
 * Where a cloud backend is, what it serves, and how igni server signs in
 * to it. Nothing to probe first: a cloud's models are typed, by the name
 * callers use, and mapped to the provider's own name where they differ.
 * The add dialog and the edit page both use it; on the edit page a secret
 * already stored can be left blank.
 */
export function CloudBackendFields({
  draft,
  methods,
  onChange,
  secretStored = false,
}: {
  draft: NodeDraft
  methods: AuthMethodInfo[]
  onChange: (change: Partial<NodeDraft>) => void
  secretStored?: boolean
}) {
  const kind = KINDS[draft.kind]
  return (
    <Stack gap="normal">
      <AddressFields draft={draft} onChange={onChange} />

      <FormControl required>
        <FormControl.Label>Models</FormControl.Label>
        <TextInput
          value={draft.modelsText}
          placeholder={kind.modelPlaceholder}
          onChange={(e) => onChange({ modelsText: e.target.value })}
          block
        />
        <FormControl.Caption>Comma-separated: the names igni&apos;s users will ask for.</FormControl.Caption>
      </FormControl>

      <FormControl>
        <FormControl.Label>Provider&apos;s names</FormControl.Label>
        <Textarea
          value={draft.modelIdsText}
          placeholder={kind.providerNameExample}
          onChange={(e) => onChange({ modelIdsText: e.target.value })}
          rows={2}
          block
        />
        <FormControl.Caption>
          One <code>name = provider-name</code> per line, where the provider knows a model by another name —{' '}
          {kind.providerNameHint}.
        </FormControl.Caption>
      </FormControl>

      <AuthFields
        methods={methods}
        value={draft.auth}
        onChange={(auth) => onChange({ auth })}
        secretStored={secretStored}
      />
    </Stack>
  )
}

/** A project and region, or an endpoint — whichever the kind is addressed by. */
function AddressFields({ draft, onChange }: { draft: NodeDraft; onChange: (change: Partial<NodeDraft>) => void }) {
  const kind = KINDS[draft.kind]
  if (kind.address === 'project-region') {
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
        <FormControl required>
          <FormControl.Label>Region</FormControl.Label>
          <TextInput
            value={draft.region}
            placeholder={kind.regionPlaceholder}
            onChange={(e) => onChange({ region: e.target.value })}
          />
          <FormControl.Caption>Or global.</FormControl.Caption>
        </FormControl>
      </Stack>
    )
  }
  return (
    <FormControl required>
      <FormControl.Label>Endpoint</FormControl.Label>
      <TextInput
        value={draft.baseUrl}
        placeholder={kind.endpointPlaceholder}
        onChange={(e) => onChange({ baseUrl: e.target.value })}
        block
      />
      <FormControl.Caption>{kind.endpointCaption}</FormControl.Caption>
    </FormControl>
  )
}
