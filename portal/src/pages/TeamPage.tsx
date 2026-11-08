import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Banner, PageHeader, Stack, Text } from '@primer/react'
import { Blankslate } from '@primer/react/experimental'
import { PeopleIcon } from '@primer/octicons-react'

import client from '../api/client'
import { InviteForm } from '../components/team/InviteForm'
import type { MemberActionHandlers } from '../components/team/MemberActions'
import { MembersTable } from '../components/team/MembersTable'
import { MemberStats } from '../components/team/MemberStats'
import { RemoveMemberDialog } from '../components/team/RemoveMemberDialog'
import { RevokeSessionsDialog } from '../components/team/RevokeSessionsDialog'
import { TeamPageSkeleton } from '../components/team/TeamPageSkeleton'
import { useMemberMutations } from '../components/team/useMemberMutations'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import { useTransientFeedback } from '../hooks/useTransientFeedback'
import type { TeamMember, TeamMemberList, UserMe } from '../types'
import { countOf } from '../utils/plural'
import './TeamPage.css'

/** The one confirmation dialog that can be open, and whom it is about. */
interface PendingConfirmation {
  action: 'remove' | 'revoke'
  member: TeamMember
}

export function TeamPage() {
  useDocumentTitle('Team')

  const meQuery = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
  })

  const teamQuery = useQuery({
    queryKey: ['team-members'],
    queryFn: () => client.get<TeamMemberList>('/portal/team').then((r) => r.data),
  })

  const { feedback, show: showFeedback, dismiss: dismissFeedback } = useTransientFeedback()
  const [pending, setPending] = useState<PendingConfirmation | null>(null)
  const mutations = useMemberMutations(showFeedback)

  if (meQuery.isLoading || teamQuery.isLoading) return <TeamPageSkeleton />

  const currentUser = meQuery.data
  const isAdmin = currentUser?.role === 'admin'
  const members = teamQuery.data?.members ?? []
  const closeDialog = () => setPending(null)

  const handlers: MemberActionHandlers = {
    onChangeRole: mutations.changeRole,
    onToggleStatus: mutations.toggleStatus,
    onRevokeSessions: (member) => setPending({ action: 'revoke', member }),
    onRemove: (member) => setPending({ action: 'remove', member }),
  }

  return (
    <Stack gap="spacious">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.Title as="h1">Team</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text size="medium">{countOf(members.length, 'member')}</Text>
        </PageHeader.Description>
      </PageHeader>

      {teamQuery.isError && (
        <Banner variant="critical" title="Failed to load team members" description="Please try refreshing the page." />
      )}

      {feedback && <Banner variant={feedback.variant} title={feedback.message} onDismiss={dismissFeedback} />}

      <MemberStats members={members} />

      {isAdmin && (
        <InviteForm onSuccess={(msg) => showFeedback('success', msg)} onError={(msg) => showFeedback('critical', msg)} />
      )}

      {members.length > 0 ? (
        <MembersTable members={members} selfId={currentUser?.user_id} handlers={isAdmin ? handlers : null} />
      ) : (
        <Blankslate spacious>
          <Blankslate.Visual>
            <PeopleIcon size={24} />
          </Blankslate.Visual>
          <Blankslate.Heading>No team members yet</Blankslate.Heading>
          <Blankslate.Description>Invite your team to start collaborating.</Blankslate.Description>
        </Blankslate>
      )}

      {pending?.action === 'revoke' && (
        <RevokeSessionsDialog
          member={pending.member}
          onClose={(confirmed) => {
            // Stays open until the request settles; the outcome is reported
            // in the banner either way.
            if (confirmed) mutations.revokeSessions(pending.member, closeDialog)
            else closeDialog()
          }}
        />
      )}

      {pending?.action === 'remove' && (
        <RemoveMemberDialog
          member={pending.member}
          onClose={(confirmed) => {
            closeDialog()
            if (confirmed) mutations.remove(pending.member)
          }}
        />
      )}
    </Stack>
  )
}
