import { FormControl, Select, Stack, Textarea, TextInput } from '@primer/react'

import type { AuthMethodInfo } from '../../types'
import { type AuthDraft, METHODS, SECRET_STORED, SETTINGS } from './auth'

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
  const form = METHODS[value.method]

  function choose(method: string) {
    const chosen = methods.find((m) => m.method === method)
    // A new method starts empty: another method's settings and secret mean nothing to it.
    if (chosen) onChange({ method: chosen.method, settings: {}, secret: '' })
  }

  return (
    <Stack gap="normal">
      <FormControl required>
        <FormControl.Label>Authentication</FormControl.Label>
        <Select value={value.method} onChange={(e) => choose(e.target.value)} block>
          {methods.map((m) => (
            <Select.Option key={m.method} value={m.method}>
              {METHODS[m.method].label}
            </Select.Option>
          ))}
        </Select>
        <FormControl.Caption>{form.caption}</FormControl.Caption>
      </FormControl>

      {[...(info?.settings ?? []), ...(info?.optional ?? [])].map((name) => (
        <FormControl key={name} required={info?.settings.includes(name)}>
          <FormControl.Label>{SETTINGS[name].label}</FormControl.Label>
          <TextInput
            value={value.settings[name] ?? ''}
            placeholder={SETTINGS[name].placeholder}
            onChange={(e) => onChange({ ...value, settings: { ...value.settings, [name]: e.target.value } })}
            block
          />
        </FormControl>
      ))}

      {info?.secret && (
        <FormControl required={!secretStored}>
          <FormControl.Label>{form.secretLabel}</FormControl.Label>
          {form.secretIsMultiline ? (
            <Textarea
              value={value.secret}
              placeholder={secretStored ? SECRET_STORED : '{"type": "service_account", ...}'}
              onChange={(e) => onChange({ ...value, secret: e.target.value })}
              rows={5}
              block
            />
          ) : (
            <TextInput
              type="password"
              value={value.secret}
              placeholder={secretStored ? SECRET_STORED : undefined}
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
