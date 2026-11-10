import { useEffect, useState } from 'react'
import { Banner, Button, Stack, Text, TextInput } from '@primer/react'
import { CheckIcon, CopyIcon } from '@primer/octicons-react'

import type { InvitedMember } from '../../types'

const COPIED_FOR_MS = 2000

/**
 * The link to pass on when we could not send it.
 *
 * Shown as a panel rather than a toast because the admin has to *act* on
 * it — copy it, paste it into Slack — and a message that dismisses
 * itself after three seconds is the wrong shape for that. It stays until
 * the next invite replaces it.
 */
export function UnsentInvite({ invite, onDismiss }: { invite: InvitedMember; onDismiss: () => void }) {
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!copied) return
    const timer = setTimeout(() => setCopied(false), COPIED_FOR_MS)
    return () => clearTimeout(timer)
  }, [copied])

  const copy = async () => {
    await navigator.clipboard.writeText(invite.invite_url)
    setCopied(true)
  }

  return (
    <Banner
      variant="warning"
      title="No email was sent"
      onDismiss={onDismiss}
      description={
        <Stack gap="condensed">
          <Text size="small">
            This deployment has no SMTP relay configured, so {invite.email} has not been notified. Send them this
            link — it is how they claim the invite, and it is safe to share.
          </Text>
          <Stack direction="horizontal" gap="condensed" align="center" wrap="wrap">
            <TextInput value={invite.invite_url} readOnly monospace className="invite-url-field" />
            <Button leadingVisual={copied ? CheckIcon : CopyIcon} onClick={copy}>
              {copied ? 'Copied' : 'Copy link'}
            </Button>
          </Stack>
        </Stack>
      }
    />
  )
}
