import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, FormControl, Stack, Text, TextInput } from '@primer/react'

import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import type { OnFeedback } from './feedback'
import { useAppHold } from './useAppHold'

/** A legal hold on the app: placed with a reason, released when it no longer applies. */
export function AppLegalHold({ appId, orgId, onFeedback }: { appId: string; orgId: string; onFeedback: OnFeedback }) {
  const queryClient = useQueryClient()
  const hold = useAppHold(orgId, appId)
  const [reason, setReason] = useState('')
  const failed = (error: unknown) => onFeedback('critical', refusalOf(error))

  const place = useMutation({
    mutationFn: () => client.put(`/portal/orgs/${orgId}/legal-holds/apps/${appId}`, { reason: reason.trim() }),
    onSuccess: () => {
      setReason('')
      queryClient.invalidateQueries({ queryKey: ['legal-holds', orgId] })
      onFeedback('success', 'Legal hold placed. Nothing of this app is erased while it stands, even once it is discarded.')
    },
    onError: failed,
  })
  const release = useMutation({
    mutationFn: () => client.delete(`/portal/orgs/${orgId}/legal-holds/apps/${appId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['legal-holds', orgId] })
      onFeedback('success', 'Legal hold released.')
    },
    onError: failed,
  })

  return (
    <Stack gap="condensed">
      <Text weight="semibold">Legal hold</Text>
      {hold ? (
        <>
          <Text size="small">
            Held since {new Date(hold.placed_at).toLocaleDateString()}: {hold.reason}. Nothing of this app&apos;s VM or
            its spaces is erased while the hold stands — not after inactivity, not when it is discarded.
          </Text>
          <Stack direction="horizontal">
            <Button onClick={() => release.mutate()} disabled={release.isPending}>
              Release legal hold
            </Button>
          </Stack>
        </>
      ) : (
        <form
          aria-label="Place a legal hold"
          onSubmit={(e) => {
            e.preventDefault()
            place.mutate()
          }}
        >
          <Stack direction="horizontal" gap="normal" align="end" wrap="wrap">
            <FormControl>
              <FormControl.Label>Reason for a legal hold</FormControl.Label>
              <FormControl.Caption>While it stands, nothing of this app&apos;s VM or its spaces is erased.</FormControl.Caption>
              <TextInput value={reason} onChange={(e) => setReason(e.target.value)} />
            </FormControl>
            <Button type="submit" disabled={!reason.trim() || place.isPending || !orgId}>
              Place legal hold
            </Button>
          </Stack>
        </form>
      )}
    </Stack>
  )
}
