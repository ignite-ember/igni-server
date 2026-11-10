import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ActionList, ActionMenu, Button, FormControl, Heading, Stack, TextInput } from '@primer/react'
import { PersonAddIcon } from '@primer/octicons-react'

import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import type { InvitedMember, TeamRole } from '../../types'
import { roleLabel } from './roles'
import { UnsentInvite } from './UnsentInvite'

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

/** The default first: most invites are for members. */
const INVITE_ROLES: readonly TeamRole[] = ['member', 'admin']

interface InviteRequest {
  email: string
  role: TeamRole
}

/** Why `email` cannot be sent as an invite, or null when it can. */
function emailProblem(email: string): string | null {
  if (!email) return 'Email is required'
  if (!EMAIL_PATTERN.test(email)) return 'Enter a valid email address'
  return null
}

export function InviteForm({ onSuccess, onError }: { onSuccess: (msg: string) => void; onError: (msg: string) => void }) {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<TeamRole>('member')
  const [emailError, setEmailError] = useState<string | null>(null)
  const [unsent, setUnsent] = useState<InvitedMember | null>(null)

  const inviteMutation = useMutation({
    mutationFn: (payload: InviteRequest) =>
      client.post<InvitedMember>('/portal/team/invite', payload).then((r) => r.data),
    onSuccess: (invited) => {
      queryClient.invalidateQueries({ queryKey: ['team-members'] })
      // The membership was created either way — only the notification
      // differs. Saying "invite sent" when nothing was sent is the
      // failure this replaces.
      if (invited.emailed) {
        setUnsent(null)
        onSuccess(`Invite sent to ${invited.email}.`)
      } else {
        setUnsent(invited)
      }
      setEmail('')
      setEmailError(null)
    },
    onError: (err: unknown) => onError(refusalOf(err, 'Failed to send invite.')),
  })

  const handleSend = () => {
    const trimmed = email.trim()
    const problem = emailProblem(trimmed)
    setEmailError(problem)
    if (problem) return
    inviteMutation.mutate({ email: trimmed, role })
  }

  return (
    <Stack className="invite-card" padding="normal" gap="normal">
      <Heading as="h2" variant="small">
        Invite team member
      </Heading>
      {unsent && <UnsentInvite invite={unsent} onDismiss={() => setUnsent(null)} />}
      <Stack direction="horizontal" gap="normal" align="end" wrap="wrap">
        <FormControl className="invite-email-field" required>
          <FormControl.Label>Email address</FormControl.Label>
          <TextInput
            type="email"
            placeholder="colleague@company.com"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value)
              if (emailError) setEmailError(null)
            }}
            validationStatus={emailError ? 'error' : undefined}
            contrast
          />
          {emailError && <FormControl.Validation variant="error">{emailError}</FormControl.Validation>}
        </FormControl>
        <FormControl>
          <FormControl.Label>Role</FormControl.Label>
          <ActionMenu>
            <ActionMenu.Button>{roleLabel(role)}</ActionMenu.Button>
            <ActionMenu.Overlay>
              <ActionList selectionVariant="single">
                {INVITE_ROLES.map((option) => (
                  <ActionList.Item key={option} selected={role === option} onSelect={() => setRole(option)}>
                    {roleLabel(option)}
                  </ActionList.Item>
                ))}
              </ActionList>
            </ActionMenu.Overlay>
          </ActionMenu>
        </FormControl>
        <Button
          variant="primary"
          leadingVisual={PersonAddIcon}
          onClick={handleSend}
          loading={inviteMutation.isPending}
          disabled={inviteMutation.isPending}
        >
          Send invite
        </Button>
      </Stack>
    </Stack>
  )
}
