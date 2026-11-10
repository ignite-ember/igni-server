import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, FormControl, Stack, Textarea } from '@primer/react'

import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import { parsePublicJwks } from '../../lib/publicJwks'
import type { OrgApp } from '../../types'
import type { OnFeedback } from './feedback'

/** A key rotation: the app's new public key replaces its old one at once. */
export function ReplaceAppKeys({ appId, onFeedback }: { appId: string; onFeedback: OnFeedback }) {
  const queryClient = useQueryClient()
  const [newKey, setNewKey] = useState('')
  const rotate = useMutation({
    mutationFn: (jwks: { keys: Record<string, unknown>[] }) => client.put<OrgApp>(`/portal/apps/${appId}/keys`, { jwks }),
    onSuccess: () => {
      setNewKey('')
      queryClient.invalidateQueries({ queryKey: ['app', appId] })
      onFeedback('success', 'Keys replaced. The old key no longer gets tokens.')
    },
    onError: (error) => onFeedback('critical', refusalOf(error)),
  })

  return (
    <form
      aria-label="Replace keys"
      onSubmit={(e) => {
        e.preventDefault()
        const jwks = parsePublicJwks(newKey)
        if (typeof jwks === 'string') onFeedback('critical', jwks)
        else rotate.mutate(jwks)
      }}
    >
      <Stack gap="condensed">
        <FormControl>
          <FormControl.Label>Replace keys</FormControl.Label>
          <FormControl.Caption>The app&apos;s new public key. Its old key stops getting tokens at once.</FormControl.Caption>
          <Textarea value={newKey} onChange={(e) => setNewKey(e.target.value)} rows={3} block />
        </FormControl>
        <Stack direction="horizontal">
          <Button type="submit" disabled={!newKey.trim() || rotate.isPending}>
            Replace keys
          </Button>
        </Stack>
      </Stack>
    </form>
  )
}
