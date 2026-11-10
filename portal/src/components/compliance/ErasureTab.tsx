import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Banner, Button, FormControl, Stack, Text, TextInput } from '@primer/react'
import { AlertIcon } from '@primer/octicons-react'

import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import type { ErasureResult } from '../../types'
import { countOf } from '../../utils/plural'

/**
 * Redacts one address from stored provider payloads.
 *
 * It edits rows in place and cannot be undone, so the address must be
 * typed twice before the button enables.
 */
export function ErasureTab() {
  const [email, setEmail] = useState('')
  const [confirmation, setConfirmation] = useState('')

  const erase = useMutation({
    mutationFn: (address: string) =>
      client
        .post<ErasureResult>('/portal/erasure', { email: address })
        .then((r) => r.data),
  })

  const matches = email.trim() !== '' && email.trim() === confirmation.trim()

  return (
    <Stack gap="normal">
      <Banner
        variant="warning"
        title="This rewrites stored data and cannot be undone"
        description={
          'Redacts one person from the raw provider payloads igni keeps — their address, ' +
          'their name where it sits beside it, and their known provider logins. Every ' +
          'other contributor in the same events is left intact, so history stays ' +
          'readable. GDPR Article 17.'
        }
      />

      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (matches) erase.mutate(email.trim())
        }}
      >
        <Stack gap="normal">
          <FormControl>
            <FormControl.Label>Email address to redact</FormControl.Label>
            <TextInput
              type="email"
              value={email}
              placeholder="person@example.com"
              onChange={(e) => setEmail(e.target.value)}
            />
          </FormControl>

          <FormControl>
            <FormControl.Label>Type it again to confirm</FormControl.Label>
            <TextInput
              type="email"
              value={confirmation}
              onChange={(e) => setConfirmation(e.target.value)}
            />
            <FormControl.Caption>
              A wrong address here redacts nobody and reports success, so it is worth
              the second look.
            </FormControl.Caption>
          </FormControl>

          <Stack direction="horizontal" gap="condensed" align="center">
            <Button
              type="submit"
              variant="danger"
              leadingVisual={AlertIcon}
              disabled={!matches || erase.isPending}
            >
              {erase.isPending ? 'Redacting…' : 'Redact from stored payloads'}
            </Button>
            {email.trim() && !matches && (
              <Text size="small" className="compliance-muted">
                The two addresses do not match yet.
              </Text>
            )}
          </Stack>
        </Stack>
      </form>

      {erase.isError && (
        <Banner
          variant="critical"
          title="The redaction did not run"
          description={refusalOf(erase.error)}
        />
      )}

      {erase.data && <ErasureOutcome result={erase.data} />}
    </Stack>
  )
}

/** "Redacted nothing" and "redacted some" look different: the first is often a misspelling. */
function ErasureOutcome({ result }: { result: ErasureResult }) {
  if (result.events_redacted === 0) {
    return (
      <Banner
        variant="info"
        title="Nothing to redact"
        description={
          'No stored payload mentions that address. Check the spelling — this is also what a ' +
          'correct request looks like when the payloads have already been pruned by retention.'
        }
      />
    )
  }
  return (
    <Banner
      variant="success"
      title={`Redacted from ${countOf(result.events_redacted, 'stored event')}`}
      description={`Matched on the address and ${countOf(result.logins_included, 'known provider login')}.`}
    />
  )
}
