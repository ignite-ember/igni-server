import { useMutation, useQueryClient } from '@tanstack/react-query'

import client from '../../api/client'
import type { FeedbackVariant } from '../../hooks/useTransientFeedback'
import type { TeamMember, TeamMemberStatus, TeamRole } from '../../types'

interface RoleChange {
  userId: string
  role: TeamRole
}

interface StatusChange {
  userId: string
  status: TeamMemberStatus
}

type ToggledStatus = Extract<TeamMemberStatus, 'active' | 'deactivated'>

/** Deactivating an active member, reactivating anyone else. */
function toggledStatus(status: TeamMemberStatus): ToggledStatus {
  return status === 'active' ? 'deactivated' : 'active'
}

const TEAM_KEY = ['team-members']

/**
 * What an admin can do to one member, each reporting its outcome through
 * `report`.
 */
export function useMemberMutations(report: (variant: FeedbackVariant, message: string) => void) {
  const queryClient = useQueryClient()
  const refreshTeam = () => queryClient.invalidateQueries({ queryKey: TEAM_KEY })

  const roleMutation = useMutation({
    mutationFn: ({ userId, role }: RoleChange) => client.patch(`/portal/team/${userId}/role`, { role }),
    onSuccess: (_data, { role }) => {
      refreshTeam()
      report('success', `Role updated to ${role}`)
    },
    onError: () => report('critical', 'Failed to update role. Please try again.'),
  })

  const statusMutation = useMutation({
    mutationFn: ({ userId, status }: StatusChange) => client.patch(`/portal/team/${userId}/status`, { status }),
    onSuccess: (_data, { status }) => {
      refreshTeam()
      report('success', `Member ${status === 'active' ? 'reactivated' : 'deactivated'}`)
    },
    onError: () => report('critical', 'Failed to update status. Please try again.'),
  })

  // DEC-10/B5. Deactivating a member stops them reaching this
  // organisation on their next request; it does not withdraw the token
  // they are holding, which still authenticates them as themselves. This
  // is the other lever, and it is the one for a leaked credential.
  const revokeMutation = useMutation({
    mutationFn: (userId: string) => client.post(`/portal/team/${userId}/revoke-sessions`),
    // No refresh: the watermark is not part of the team listing and
    // inventing a "sessions" column to refresh would imply a session
    // count the design deliberately does not keep.
    onSuccess: () => report('success', 'Signed out of every device. They can sign in again straight away.'),
    onError: () => report('critical', 'Failed to sign that member out. Please try again.'),
  })

  const removeMutation = useMutation({
    mutationFn: (userId: string) => client.delete(`/portal/team/${userId}`),
    onSuccess: () => {
      refreshTeam()
      report('success', 'Member removed from team')
    },
    onError: () => report('critical', 'Failed to remove member. Please try again.'),
  })

  return {
    changeRole: (member: TeamMember, role: TeamRole) => roleMutation.mutate({ userId: member.id, role }),
    toggleStatus: (member: TeamMember) =>
      statusMutation.mutate({ userId: member.id, status: toggledStatus(member.status) }),
    revokeSessions: (member: TeamMember, onSettled: () => void) => revokeMutation.mutate(member.id, { onSettled }),
    remove: (member: TeamMember) => removeMutation.mutate(member.id),
  }
}
