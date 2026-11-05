import { useState, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import {
  ActionMenu,
  ActionList,
  Banner,
  Button,
  ConfirmationDialog,
  FormControl,
  Heading,
  IconButton,
  Label,
  PageHeader,
  RelativeTime,
  Stack,
  StateLabel,
  Text,
  TextInput,
  VisuallyHidden,
} from '@primer/react'
import { DataTable, Table } from '@primer/react/experimental'
import { Blankslate } from '@primer/react/experimental'
import {
  CheckIcon,
  CopyIcon,
  KebabHorizontalIcon,
  PeopleIcon,
  PersonAddIcon,
  ShieldCheckIcon,
  ShieldIcon,
  ArrowSwitchIcon,
  NoEntryIcon,
  CheckCircleIcon,
  SignOutIcon,
  TrashIcon,
} from '@primer/octicons-react'
import client from '../api/client'
import { SkeletonText } from '@primer/react/experimental'
import type { InvitedMember, TeamMember, TeamMemberList, TeamRole, TeamMemberStatus, UserMe } from '../types'
import './TeamPage.css'
import { countOf } from '../utils/plural'

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return n.toLocaleString()
}

function RoleBadge({ role }: { role: TeamRole }) {
  switch (role) {
    case 'admin':
      return <Label variant="accent"><span className="role-badge-inner"><ShieldCheckIcon size={12} /> Admin</span></Label>
    case 'member':
      return <Label><span className="role-badge-inner"><ShieldIcon size={12} /> Member</span></Label>
  }
}

function StatusBadge({ status }: { status: TeamMemberStatus }) {
  switch (status) {
    case 'active':
      return <StateLabel status="issueOpened" variant="small">Active</StateLabel>
    case 'invited':
      // `issueDraft` renders a hollow dashed circle — empty in the middle,
      // signalling "pending acceptance" without implying success or failure.
      return <StateLabel status="issueDraft" variant="small">Invited</StateLabel>
    case 'deactivated':
      return <StateLabel status="issueClosedNotPlanned" variant="small">Deactivated</StateLabel>
  }
}


/**
 * The link to pass on when we could not send it.
 *
 * Shown as a panel rather than a toast because the admin has to *act* on
 * it — copy it, paste it into Slack — and a message that dismisses
 * itself after three seconds is the wrong shape for that. It stays until
 * the next invite replaces it.
 */
function UnsentInvite({ invite, onDismiss }: { invite: InvitedMember; onDismiss: () => void }) {
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    await navigator.clipboard.writeText(invite.invite_url)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <Banner
      variant="warning"
      title="No email was sent"
      onDismiss={onDismiss}
      description={
        <Stack gap="condensed">
          <Text size="small">
            This deployment has no SMTP relay configured, so {invite.email} has not been
            notified. Send them this link — it is how they claim the invite, and it is safe
            to share.
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

export function InviteForm({ onSuccess, onError }: { onSuccess: (msg: string) => void; onError: (msg: string) => void }) {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<TeamRole>('member')
  const [emailError, setEmailError] = useState<string | null>(null)
  const [unsent, setUnsent] = useState<InvitedMember | null>(null)

  const inviteMutation = useMutation({
    mutationFn: (payload: { email: string; role: TeamRole }) =>
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
    onError: (err: unknown) => {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Failed to send invite.'
      onError(typeof detail === 'string' ? detail : 'Failed to send invite.')
    },
  })

  const validate = (): boolean => {
    const trimmed = email.trim()
    if (!trimmed) {
      setEmailError('Email is required')
      return false
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed)) {
      setEmailError('Enter a valid email address')
      return false
    }
    setEmailError(null)
    return true
  }

  const handleSend = () => {
    if (!validate()) return
    inviteMutation.mutate({ email: email.trim(), role })
  }

  return (
    <Stack className="invite-card" padding="normal" gap="normal">
      <Heading as="h2" variant="small">Invite team member</Heading>
      {unsent && <UnsentInvite invite={unsent} onDismiss={() => setUnsent(null)} />}
      <Stack direction="horizontal" gap="normal" align="end" wrap="wrap">
        <FormControl className="invite-email-field" required>
          <FormControl.Label>Email address</FormControl.Label>
          <TextInput
            type="email"
            placeholder="colleague@company.com"
            value={email}
            onChange={(e) => { setEmail(e.target.value); if (emailError) setEmailError(null) }}
            validationStatus={emailError ? 'error' : undefined}
            contrast
          />
          {emailError && (
            <FormControl.Validation variant="error">{emailError}</FormControl.Validation>
          )}
        </FormControl>
        <FormControl>
          <FormControl.Label>Role</FormControl.Label>
          <ActionMenu>
            <ActionMenu.Button>{role.charAt(0).toUpperCase() + role.slice(1)}</ActionMenu.Button>
            <ActionMenu.Overlay>
              <ActionList selectionVariant="single">
                {(['member', 'admin'] as TeamRole[]).map((r) => (
                  <ActionList.Item
                    key={r}
                    selected={role === r}
                    onSelect={() => setRole(r)}
                  >
                    {r.charAt(0).toUpperCase() + r.slice(1)}
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

function MemberStats({ members }: { members: TeamMember[] }) {
  const active = members.filter((m) => m.status === 'active').length
  const invited = members.filter((m) => m.status === 'invited').length
  const admins = members.filter((m) => m.role === 'admin' && m.status === 'active').length
  const totalTokens = members.reduce((sum, m) => sum + m.total_tokens, 0)

  return (
    <Stack direction="horizontal" gap="normal" wrap="wrap">
      <Stack className="team-stat" padding="normal" gap="condensed">
        <Text size="small" className="team-muted">Active members</Text>
        <Text size="medium" weight="semibold">{active}</Text>
      </Stack>
      <Stack className="team-stat" padding="normal" gap="condensed">
        <Text size="small" className="team-muted">Pending invites</Text>
        <Text size="medium" weight="semibold">{invited}</Text>
      </Stack>
      <Stack className="team-stat" padding="normal" gap="condensed">
        <Text size="small" className="team-muted">Admins</Text>
        <Text size="medium" weight="semibold">{admins}</Text>
      </Stack>
      <Stack className="team-stat" padding="normal" gap="condensed">
        <Text size="small" className="team-muted">Org tokens (30d)</Text>
        <Text size="medium" weight="semibold">{formatTokens(totalTokens)}</Text>
      </Stack>
    </Stack>
  )
}

const ROLE_OPTIONS: { role: TeamRole; label: string; icon: typeof ShieldCheckIcon }[] = [
  { role: 'admin', label: 'Admin', icon: ShieldCheckIcon },
  { role: 'member', label: 'Member', icon: ShieldIcon },
]

export function TeamPage() {
  useDocumentTitle('Team')
  const queryClient = useQueryClient()

  const meQuery = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
  })

  const teamQuery = useQuery({
    queryKey: ['team-members'],
    queryFn: () => client.get<TeamMemberList>('/portal/team').then((r) => r.data),
  })

  const currentUser = meQuery.data
  const isAdmin = currentUser?.role === 'admin'
  const members = teamQuery.data?.members ?? []

  const [confirmRemove, setConfirmRemove] = useState<TeamMember | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [confirmRevoke, setConfirmRevoke] = useState<TeamMember | null>(null)
  const [feedbackBanner, setFeedbackBanner] = useState<{ variant: 'success' | 'critical'; message: string } | null>(null)

  const showFeedback = useCallback((variant: 'success' | 'critical', message: string) => {
    setFeedbackBanner({ variant, message })
    setTimeout(() => setFeedbackBanner(null), 5000)
  }, [])

  const roleMutation = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: string }) =>
      client.patch(`/portal/team/${userId}/role`, { role }),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['team-members'] })
      showFeedback('success', `Role updated to ${variables.role}`)
    },
    onError: () => showFeedback('critical', 'Failed to update role. Please try again.'),
  })

  const statusMutation = useMutation({
    mutationFn: ({ userId, status }: { userId: string; status: string }) =>
      client.patch(`/portal/team/${userId}/status`, { status }),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['team-members'] })
      showFeedback('success', `Member ${variables.status === 'active' ? 'reactivated' : 'deactivated'}`)
    },
    onError: () => showFeedback('critical', 'Failed to update status. Please try again.'),
  })


  // DEC-10/B5. Deactivating a member stops them reaching this
  // organisation on their next request; it does not withdraw the token
  // they are holding, which still authenticates them as themselves. This
  // is the other lever, and it is the one for a leaked credential.
  const revokeMutation = useMutation({
    mutationFn: (userId: string) =>
      client.post(`/portal/team/${userId}/revoke-sessions`),
    onSuccess: () => {
      // No `invalidateQueries`: the watermark is not part of the team
      // listing and inventing a "sessions" column to refresh would imply
      // a session count the design deliberately does not keep.
      setConfirmRevoke(null)
      showFeedback('success', 'Signed out of every device. They can sign in again straight away.')
    },
    onError: () => {
      setConfirmRevoke(null)
      showFeedback('critical', 'Failed to sign that member out. Please try again.')
    },
  })

  const removeMutation = useMutation({
    mutationFn: (userId: string) =>
      client.delete(`/portal/team/${userId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['team-members'] })
      setConfirmRemove(null)
      showFeedback('success', 'Member removed from team')
    },
    onError: () => {
      setConfirmRemove(null)
      showFeedback('critical', 'Failed to remove member. Please try again.')
    },
  })

  const handleRoleChange = useCallback((userId: string, newRole: TeamRole) => {
    roleMutation.mutate({ userId, role: newRole })
  }, [roleMutation])

  const handleToggleStatus = useCallback((userId: string, currentStatus: string) => {
    const newStatus = currentStatus === 'active' ? 'deactivated' : 'active'
    statusMutation.mutate({ userId, status: newStatus })
  }, [statusMutation])


  const handleRemove = useCallback((userId: string) => {
    removeMutation.mutate(userId)
  }, [removeMutation])

  if (meQuery.isLoading || teamQuery.isLoading) {
    return (
      <Stack gap="spacious">
        <PageHeader>
          <PageHeader.TitleArea>
            <PageHeader.Title as="h1">Team</PageHeader.Title>
          </PageHeader.TitleArea>
          <PageHeader.Description>
            <SkeletonText size="bodyMedium" maxWidth={100} />
          </PageHeader.Description>
        </PageHeader>
        <Stack direction="horizontal" gap="normal" wrap="wrap">
          {Array.from({ length: 4 }).map((_, i) => (
            <Stack key={i} className="team-stat" padding="normal" gap="condensed">
              <SkeletonText size="bodySmall" maxWidth={100} />
              <SkeletonText size="bodyMedium" maxWidth={40} />
            </Stack>
          ))}
        </Stack>
        <Stack gap="none">
          {Array.from({ length: 4 }).map((_, i) => (
            <Stack key={i} direction="horizontal" gap="normal" padding="normal" style={{ borderBottom: '1px solid var(--borderColor-muted)' }}>
              <SkeletonText size="bodyMedium" maxWidth={160} />
              <SkeletonText size="bodySmall" maxWidth={60} />
              <SkeletonText size="bodySmall" maxWidth={50} />
              <SkeletonText size="bodySmall" maxWidth={40} />
              <SkeletonText size="bodySmall" maxWidth={60} />
              <SkeletonText size="bodySmall" maxWidth={80} />
              <SkeletonText size="bodySmall" maxWidth={80} />
            </Stack>
          ))}
        </Stack>
      </Stack>
    )
  }

  const rows = members.map((m) => ({ ...m }))

  return (
    <Stack gap="spacious">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.Title as="h1">Team</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text size="medium">
            {countOf(members.length, 'member')}
          </Text>
        </PageHeader.Description>
      </PageHeader>

      {teamQuery.isError && (
        <Banner
          variant="critical"
          title="Failed to load team members"
          description="Please try refreshing the page."
        />
      )}

      {feedbackBanner && (
        <Banner
          variant={feedbackBanner.variant}
          title={feedbackBanner.message}
          onDismiss={() => setFeedbackBanner(null)}
        />
      )}

      <MemberStats members={members} />

      {isAdmin && <InviteForm onSuccess={(msg) => showFeedback('success', msg)} onError={(msg) => showFeedback('critical', msg)} />}

      {rows.length > 0 ? (
        <Table.Container className="team-table">
          <Table.Title as={"h3" as never} id="team-title">Members</Table.Title>
          <DataTable
            aria-labelledby="team-title"
            data={rows}
            columns={[
              {
                header: 'Member',
                field: 'full_name',
                rowHeader: true,
                renderCell: (row) => (
                  <Stack gap="none">
                    <Text weight="semibold">{row.full_name ?? row.email}</Text>
                    {row.full_name && <Text size="small" className="team-muted">{row.email}</Text>}
                  </Stack>
                ),
              },
              {
                header: 'Role',
                field: 'role',
                renderCell: (row) => <RoleBadge role={row.role} />,
              },
              {
                header: 'Status',
                field: 'status',
                renderCell: (row) => <StatusBadge status={row.status} />,
              },
              {
                // Requests, not sessions: each chat request is a row in
                // chat usage, and nothing records a session.
                header: 'Requests (30d)',
                field: 'total_requests',
                align: 'end',
                renderCell: (row) => (
                  <Text>{row.total_requests > 0 ? row.total_requests : '—'}</Text>
                ),
              },
              {
                header: 'Tokens (30d)',
                field: 'total_tokens',
                align: 'end',
                renderCell: (row) => (
                  <Text>{row.total_tokens > 0 ? formatTokens(row.total_tokens) : '—'}</Text>
                ),
              },
              {
                header: 'Last active',
                field: 'last_active',
                renderCell: (row) =>
                  row.last_active ? <RelativeTime datetime={row.last_active} /> : <Text className="team-muted">Never</Text>,
              },
              {
                header: 'Joined',
                field: 'joined_at',
                renderCell: (row) => <RelativeTime datetime={row.joined_at} />,
              },
              ...(isAdmin
                ? [
                    {
                      id: 'actions',
                      // Named, not empty. A column header with no text
                      // is a cell a screen reader cannot describe —
                      // axe's empty-table-header. F137.
                      header: () => <VisuallyHidden>Actions</VisuallyHidden>,
                      field: 'id' as const,
                      width: 'auto' as const,
                      renderCell: (row: TeamMember) => {
                        const isSelf = row.id === currentUser?.user_id
                        const isActive = row.status === 'active'
                        const isDeactivated = row.status === 'deactivated'
                        // Only active members can be deactivated, and only
                        // deactivated members can be reactivated. `invited`
                        // members are still pending acceptance — the claim
                        // flow promotes them on signup.
                        const canToggleStatus = !isSelf && (isActive || isDeactivated)
                        // Role, status and remove are all blocked on self at
                        // the backend, so the self-row menu is empty and the
                        // groups below are gated on ``!isSelf``.
                        return (
                          <ActionMenu>
                            <ActionMenu.Anchor>
                              <IconButton
                                icon={KebabHorizontalIcon}
                                variant="invisible"
                                size="small"
                                aria-label={`Actions for ${row.full_name ?? row.email}`}
                              />
                            </ActionMenu.Anchor>
                            <ActionMenu.Overlay>
                              <ActionList>
                                {!isSelf && (
                                  <>
                                    <ActionList.Group title="Change role">
                                      {ROLE_OPTIONS.map((opt) => (
                                        <ActionList.Item
                                          key={opt.role}
                                          onSelect={() => handleRoleChange(row.id, opt.role)}
                                          disabled={row.role === opt.role}
                                        >
                                          <ActionList.LeadingVisual>
                                            <opt.icon size={16} />
                                          </ActionList.LeadingVisual>
                                          {opt.label}
                                          {row.role === opt.role && (
                                            <ActionList.TrailingVisual>
                                              <CheckCircleIcon size={16} />
                                            </ActionList.TrailingVisual>
                                          )}
                                        </ActionList.Item>
                                      ))}
                                    </ActionList.Group>
                                    <ActionList.Divider />
                                  </>
                                )}
                                {canToggleStatus && (
                                  <>
                                    <ActionList.Divider />
                                    <ActionList.Item onSelect={() => handleToggleStatus(row.id, row.status)}>
                                      <ActionList.LeadingVisual>
                                        {isActive ? <NoEntryIcon size={16} /> : <ArrowSwitchIcon size={16} />}
                                      </ActionList.LeadingVisual>
                                      {isActive ? 'Deactivate' : 'Reactivate'}
                                    </ActionList.Item>
                                  </>
                                )}
                                {!isSelf && (
                                  <>
                                    <ActionList.Divider />
                                    <ActionList.Item onSelect={() => setConfirmRevoke(row)}>
                                      <ActionList.LeadingVisual>
                                        <SignOutIcon size={16} />
                                      </ActionList.LeadingVisual>
                                      Sign out everywhere
                                      <ActionList.Description variant="block">
                                        Withdraws every token they hold. They can sign in again.
                                      </ActionList.Description>
                                    </ActionList.Item>
                                  </>
                                )}
                                {!isSelf && (
                                  <>
                                    <ActionList.Divider />
                                    <ActionList.Item
                                      variant="danger"
                                      onSelect={() => { setConfirmRemove(row); setConfirmOpen(true) }}
                                    >
                                      <ActionList.LeadingVisual>
                                        <TrashIcon size={16} />
                                      </ActionList.LeadingVisual>
                                      Remove from team
                                    </ActionList.Item>
                                  </>
                                )}
                              </ActionList>
                            </ActionMenu.Overlay>
                          </ActionMenu>
                        )
                      },
                    },
                  ]
                : []),
            ]}
          />
        </Table.Container>
      ) : (
        <Blankslate spacious>
          <Blankslate.Visual>
            <PeopleIcon size={24} />
          </Blankslate.Visual>
          <Blankslate.Heading>No team members yet</Blankslate.Heading>
          <Blankslate.Description>
            Invite your team to start collaborating.
          </Blankslate.Description>
        </Blankslate>
      )}

      {/* Sign-out-everywhere confirmation. Confirmed rather than
          immediate: it is not destructive — they can sign in again — but
          it does end whatever they were in the middle of on every
          machine, and it is one menu item away from "Deactivate". */}
      {confirmRevoke ? (
        <ConfirmationDialog
          title="Sign out of every device?"
          confirmButtonContent="Sign out everywhere"
          cancelButtonContent="Cancel"
          width="large"
          className="confirm-dialog-lg"
          onClose={(gesture) => {
            if (gesture === 'confirm') {
              revokeMutation.mutate(confirmRevoke.id)
            } else {
              setConfirmRevoke(null)
            }
          }}
        >
          <Text size="medium">
            Every token <Text weight="semibold">{confirmRevoke.full_name ?? confirmRevoke.email}</Text> currently
            holds — portal, CLI and editor — stops working immediately. They keep their account and their access to
            this organisation, and can sign in again whenever they like.
          </Text>
        </ConfirmationDialog>
      ) : null}

      {/* Remove confirmation dialog */}
      {confirmOpen && confirmRemove ? (
        <ConfirmationDialog
          title="Remove team member"
          confirmButtonContent="Remove"
          cancelButtonContent="Cancel"
          confirmButtonType="danger"
          width="large"
          className="confirm-dialog-lg"
          onClose={(gesture) => {
            setConfirmOpen(false)
            if (gesture === 'confirm') {
              handleRemove(confirmRemove.id)
            }
            setConfirmRemove(null)
          }}
        >
          <Text size="medium">
            Are you sure you want to remove <Text weight="semibold">{confirmRemove.full_name ?? confirmRemove.email}</Text> from the team? This action cannot be undone.
          </Text>
        </ConfirmationDialog>
      ) : null}
    </Stack>
  )
}
