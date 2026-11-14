import { FormControl, Select, Stack, Textarea, TextInput } from '@primer/react'

import type { AuthMethodInfo } from '../../types'
import { type AuthDraft, METHOD_LABEL, secretLabel, SETTING_LABEL } from './auth'

/**
 * How a backend authenticates: the methods its kind offers, and the
 * fields the chosen one needs — built from the server's own table, so
 * the form never offers a method the server would refuse.
 *
 * A secret goes in and never comes back. When one is already stored the
 * field says so and may be left blank to keep it.
 */
export function AuthFields({
  methods,
  value,
  onChange,
  secretStored = false,
}: {
  methods: AuthMethodInfo[]
  value: AuthDraft
  onChange: (next: AuthDraft) => void
  secretStored?: boolean
}) {
  const info = methods.find((m) => m.method === value.method)

  return (
    <Stack gap="normal">
      <FormControl required>
        <FormControl.Label>Authentication</FormControl.Label>
        <Select
          value={value.method}
          onChange={(e) =>
            // A new method starts empty: another method's settings and secret mean nothing to it.
            onChange({
              method: e.target.value as AuthDraft['method'],
              settings: {},
              secret: '',
            })
          }
          block
        >
          {methods.map((m) => (
            <Select.Option key={m.method} value={m.method}>
              {METHOD_LABEL[m.method].label}
            </Select.Option>
          ))}
        </Select>
        <FormControl.Caption>{METHOD_LABEL[value.method].caption}</FormControl.Caption>
      </FormControl>

      {info?.settings.map((name) => (
        <FormControl key={name} required>
          <FormControl.Label>{SETTING_LABEL[name]?.label ?? name}</FormControl.Label>
          <TextInput
            value={value.settings[name] ?? ''}
            placeholder={SETTING_LABEL[name]?.placeholder}
            onChange={(e) =>
              onChange({
                ...value,
                settings: { ...value.settings, [name]: e.target.value },
              })
            }
            block
          />
        </FormControl>
      ))}

      {info?.secret && (
        <FormControl required={!secretStored}>
          <FormControl.Label>{secretLabel(value.method)}</FormControl.Label>
          {value.method === 'service_account_key' ? (
            <Textarea
              value={value.secret}
              placeholder={secretStored ? 'stored — leave blank to keep it' : '{"type": "service_account", ...}'}
              onChange={(e) => onChange({ ...value, secret: e.target.value })}
              rows={5}
              block
            />
          ) : (
            <TextInput
              type="password"
              value={value.secret}
              placeholder={secretStored ? 'stored — leave blank to keep it' : undefined}
              onChange={(e) => onChange({ ...value, secret: e.target.value })}
              block
            />
          )}
          <FormControl.Caption>Stored encrypted and never shown again.</FormControl.Caption>
        </FormControl>
      )}
    </Stack>
  )
}
