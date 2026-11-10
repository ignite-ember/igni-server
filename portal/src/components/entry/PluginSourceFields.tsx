import { FormControl, Stack, Text, TextInput } from '@primer/react'
import type { PluginSource } from '../../lib/newEntryForm'

interface PluginSourceFieldsProps {
  value: PluginSource
  onChange: (next: PluginSource) => void
  /** Why the URL was refused, or null. */
  urlError: string | null
}

/** Where a plugin is installed from: a git URL, and optionally a ref and a path inside the repo. */
export function PluginSourceFields({ value, onChange, urlError }: PluginSourceFieldsProps) {
  return (
    <Stack direction="vertical" gap="normal">
      <FormControl>
        <FormControl.Label>Source URL</FormControl.Label>
        <TextInput
          value={value.url}
          onChange={(e) => onChange({ ...value, url: e.currentTarget.value })}
          placeholder="https://github.com/owner/repo"
          validationStatus={urlError ? 'error' : undefined}
          block
        />
        {urlError && <FormControl.Validation variant="error">{urlError}</FormControl.Validation>}
      </FormControl>
      <FormControl>
        <FormControl.Label>
          Source ref <Text size="small" className="group-detail-muted">(optional: branch, tag, or SHA)</Text>
        </FormControl.Label>
        <TextInput
          value={value.ref}
          onChange={(e) => onChange({ ...value, ref: e.currentTarget.value })}
          placeholder="main"
          block
        />
      </FormControl>
      <FormControl>
        <FormControl.Label>
          Source subdir <Text size="small" className="group-detail-muted">(optional: path inside the repo)</Text>
        </FormControl.Label>
        <TextInput
          value={value.subdir}
          onChange={(e) => onChange({ ...value, subdir: e.currentTarget.value })}
          placeholder="plugins/my-plugin"
          block
        />
      </FormControl>
      <Text size="small" className="group-detail-muted">
        Leave the URL empty to write the YAML content below as a legacy plugin entry. Set a URL to install the plugin
        from git on the runner instead.
      </Text>
    </Stack>
  )
}
