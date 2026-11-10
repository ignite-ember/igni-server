import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Button, Checkbox, ConfirmationDialog, FormControl, Stack, Text } from '@primer/react'

import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import type { DiscardedApp, OrgApp } from '../../types'
import type { OnFeedback } from './feedback'
import { useAppHold } from './useAppHold'

/**
 * Discarding the app, behind a confirm: removed at once, its place under
 * the licence freed, its VM erased in the background — or a fresh app
 * started in its place, which the page then opens.
 */
export function DiscardApp({ app, orgId, onFeedback }: { app: OrgApp; orgId: string; onFeedback: OnFeedback }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const hold = useAppHold(orgId, app.app_id)
  const [confirming, setConfirming] = useState(false)
  const [startFresh, setStartFresh] = useState(false)

  const discard = useMutation({
    mutationFn: (fresh: boolean) =>
      client.post<DiscardedApp>(`/portal/apps/${app.app_id}/discard`, { start_fresh: fresh }).then((r) => r.data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['apps'] })
      queryClient.invalidateQueries({ queryKey: ['erasures', orgId] })
      navigate(result.fresh_app ? `/apps/${result.fresh_app.app_id}` : '/apps')
    },
    onError: (error) => onFeedback('critical', refusalOf(error)),
  })

  return (
    <>
      <Stack direction="horizontal">
        <Button variant="danger" onClick={() => setConfirming(true)} disabled={discard.isPending}>
          Discard app
        </Button>
      </Stack>

      {confirming && (
        <ConfirmationDialog
          title={`Discard ${app.name}?`}
          confirmButtonContent="Discard app"
          cancelButtonContent="Cancel"
          confirmButtonType="danger"
          onClose={(gesture) => {
            setConfirming(false)
            if (gesture === 'confirm') discard.mutate(startFresh)
          }}
        >
          <Stack gap="normal">
            <Text>
              The app is removed now: its VM can never be woken again and its credential stops working. Its place under
              your licence is freed at once, so you can start a new app straight away. Its memory, snapshots, volume and
              keys are erased in the background — unless a legal hold is placed on it. Its conversations in your
              organisation&apos;s database are erased with it.
            </Text>
            {hold && <Text weight="semibold">A legal hold stands: its data is kept until the hold is released.</Text>}
            <FormControl>
              <Checkbox checked={startFresh} onChange={(e) => setStartFresh(e.target.checked)} />
              <FormControl.Label>Start a fresh app with the same name, key and settings</FormControl.Label>
            </FormControl>
          </Stack>
        </ConfirmationDialog>
      )}
    </>
  )
}
