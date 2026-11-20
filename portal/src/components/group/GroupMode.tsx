import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ActionList, ActionMenu, ConfirmationDialog, Stack, Text } from '@primer/react'

import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import type { GroupMode as Mode } from '../../types'
import type { ShowFeedback } from './feedback'

/** The three places igni can run, in the order the menu offers them. */
const MODES: readonly { mode: Mode; label: string; means: string }[] = [
  {
    mode: 'local_and_cloud',
    label: 'This computer and the cloud',
    means: 'People choose in the desktop app between igni on their computer and their own igni in the cloud.',
  },
  {
    mode: 'local',
    label: 'This computer only',
    means: "igni runs only on people's computers. Their cloud igni is switched off, and what it holds is kept.",
  },
  {
    mode: 'cloud',
    label: 'Cloud only',
    means: "The desktop app connects only to people's igni in the cloud. Nothing runs on their computers.",
  },
]

/** What a mode is called; an unknown one reads as the default, which is what igni treats it as. */
function specOf(mode: Mode | undefined) {
  return MODES.find((spec) => spec.mode === mode) ?? MODES[0]
}

/** What the banner says once the change is saved — including what happens to people right now. */
function savedMessage(from: Mode, to: Mode): string {
  if (to === 'local') return "Group runs igni on people's computers only"
  if (to === 'cloud' && from === 'local_and_cloud') {
    return 'Group runs igni in the cloud only. People using it on their computer move when their current run ends'
  }
  if (to === 'cloud') return 'Group runs igni in the cloud only'
  return 'People in this group choose between their computer and the cloud'
}

/**
 * Where igni runs for this group's people (design §6.1).
 *
 * The desktop app reads this from the signed group pack and from the
 * server, and the server enforces `local` itself: a local-only person's
 * cloud igni is switched off and cannot sign in. So this is a real
 * switch, not a preference the app may ignore.
 *
 * Editable on the Default group too, unlike the group's other settings.
 * Those are what igni ships; this is where it runs, the same kind of
 * deployment choice as the group's VM profile.
 *
 * Moving a group to `local` disconnects anybody using igni in the
 * cloud at once, so that one asks first. Moving to `cloud` waits for a
 * person's local run to finish, so it only says so.
 */
export function GroupMode({
  groupId,
  current,
  isAdmin,
  onFeedback,
}: {
  groupId: string
  current: Mode | undefined
  isAdmin: boolean
  onFeedback: ShowFeedback
}) {
  const queryClient = useQueryClient()
  const now = specOf(current)
  // The choice waiting on the confirmation; null while none is open.
  const [confirming, setConfirming] = useState<Mode | null>(null)

  const setMode = useMutation({
    mutationFn: (mode: Mode) => client.patch(`/portal/groups/${groupId}`, { mode }).then((r) => r.data),
    onSuccess: (_data, mode) => {
      queryClient.invalidateQueries({ queryKey: ['group', groupId] })
      onFeedback('success', savedMessage(now.mode, mode))
    },
    onError: (err: unknown) => onFeedback('critical', refusalOf(err, 'Failed to change where igni runs')),
  })

  const choose = (mode: Mode) => {
    // The current value is no change: nothing to save, nobody to warn.
    if (mode === now.mode) return
    if (mode === 'local') {
      setConfirming(mode)
      return
    }
    setMode.mutate(mode)
  }

  if (!isAdmin) {
    return (
      <Stack gap="none">
        <Text size="medium">{now.label}</Text>
        <Text size="small" className="settings-section-caption">
          {now.means}
        </Text>
      </Stack>
    )
  }

  return (
    <Stack gap="condensed" align="start">
      <ActionMenu>
        <ActionMenu.Button size="small" loading={setMode.isPending}>
          {now.label}
        </ActionMenu.Button>
        <ActionMenu.Overlay width="large">
          <ActionList selectionVariant="single">
            {MODES.map((spec) => (
              <ActionList.Item key={spec.mode} selected={spec.mode === now.mode} onSelect={() => choose(spec.mode)}>
                {spec.label}
                <ActionList.Description variant="block">{spec.means}</ActionList.Description>
              </ActionList.Item>
            ))}
          </ActionList>
        </ActionMenu.Overlay>
      </ActionMenu>
      {/* What the current choice means, without opening the menu to find out. */}
      <Text size="small" className="settings-section-caption">
        {now.means}
      </Text>

      {confirming && (
        <ConfirmationDialog
          title="Run igni on people's computers only?"
          confirmButtonContent="Switch off the cloud"
          confirmButtonType="danger"
          cancelButtonContent="Cancel"
          width="large"
          onClose={(gesture) => {
            const mode = confirming
            setConfirming(null)
            if (gesture === 'confirm') setMode.mutate(mode)
          }}
        >
          <Text size="medium">
            Anyone in this group using igni in the cloud is disconnected at once, and the desktop app goes back to
            igni on their computer. Their cloud igni is switched off; what it holds is kept, and comes back if you
            change this again.
          </Text>
        </ConfirmationDialog>
      )}
    </Stack>
  )
}
