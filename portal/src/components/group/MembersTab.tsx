import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ActionList, ActionMenu, Banner, Button, ConfirmationDialog, Stack, Text } from '@primer/react'
import { Blankslate, DataTable, Table } from '@primer/react/experimental'
import { PersonAddIcon, TrashIcon } from '@primer/octicons-react'

import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import type {
  OrgGroupList,
  OrgGroupMember,
  OrgGroupMemberList,
  OrgGroupMembershipList,
  TeamMember,
  TeamMemberList,
} from '../../types'
import { AddMemberMenu } from './AddMemberMenu'
import { invalidateMembership } from './membership'

/**
 * Who is in this group, and moving people in and out of it.
 *
 * Everybody in the organisation is in exactly one group — Default until
 * an admin puts them somewhere else — so there is no "add" that is not
 * a move: bringing somebody here takes them out of their current group,
 * and taking them out of here puts them back in Default. Nobody is taken
 * out of Default; they leave it by being moved.
 */
export function MembersTab({
  groupId,
  isAdmin,
  isDefault,
  onMoveMember,
  movePending,
  active,
}: {
  groupId: string
  isAdmin: boolean
  /** Default keeps everybody nobody has placed elsewhere: no one is removed from it. */
  isDefault: boolean
  onMoveMember: (userId: string, toGroupId: string) => void
  movePending: boolean
  active: boolean
}) {
  const queryClient = useQueryClient()

  const membersQuery = useQuery({
    queryKey: ['group-members', groupId],
    queryFn: () => client.get<OrgGroupMemberList>(`/portal/groups/${groupId}/members`).then((r) => r.data),
    enabled: active,
  })

  const teamQuery = useQuery({
    queryKey: ['team-members-select'],
    queryFn: () => client.get<TeamMemberList>('/portal/team').then((r) => r.data),
    enabled: active,
  })

  // One call for everybody's group, rather than the members of every
  // other group one request at a time.
  const membershipsQuery = useQuery({
    queryKey: ['group-memberships'],
    queryFn: () => client.get<OrgGroupMembershipList>('/portal/group-memberships').then((r) => r.data),
    enabled: active && isAdmin,
  })

  const groupsQuery = useQuery({
    queryKey: ['groups'],
    queryFn: () => client.get<OrgGroupList>('/portal/groups').then((r) => r.data),
    enabled: active && isAdmin,
  })

  const removeMutation = useMutation({
    mutationFn: (userId: string) => client.delete(`/portal/groups/${groupId}/members/${userId}`),
    onSuccess: () => invalidateMembership(queryClient),
  })

  const members: OrgGroupMember[] = membersQuery.data?.members ?? []
  const teamMembers: TeamMember[] = teamQuery.data?.members ?? []
  const otherGroups = (groupsQuery.data?.groups ?? []).filter((g) => g.id !== groupId)

  const memberUserIds = new Set(members.map((m) => m.user_id))
  const assignableMembers = teamMembers.filter((m) => m.status === 'active')
  const currentGroupOf = new Map<string, string>(
    (membershipsQuery.data?.memberships ?? [])
      .filter((x) => x.group_id !== groupId)
      .map((x) => [x.user_id, x.group_name])
  )

  const [confirmRemove, setConfirmRemove] = useState<OrgGroupMember | null>(null)

  if (!active) return null

  return (
    <Stack direction="vertical" gap="normal" padding="normal">
      {membersQuery.isError && <Banner variant="critical" title="Failed to load members" />}
      {removeMutation.isError && (
        <Banner
          variant="critical"
          title={refusalOf(removeMutation.error, 'Failed to remove member')}
        />
      )}

      {/* Says what membership does, the way the configuration tab says
          what its list does. Moving somebody is not bookkeeping — it
          changes what their agent can do. */}
      <Stack direction="horizontal" justify="space-between" align="center">
        <Text size="small" className="group-detail-muted">
          Everyone here gets this group&apos;s configuration on their next sync. Everybody is in exactly one
          group, so bringing someone here moves them out of theirs.
        </Text>
        {isAdmin && (
          <>
          {membershipsQuery.isPending ? (
            <Button disabled loading>
              <PersonAddIcon /> Move someone here
            </Button>
          ) : (
            <AddMemberMenu
              members={assignableMembers}
              memberUserIds={memberUserIds}
              currentGroupOf={currentGroupOf}
              onMove={(userId) => onMoveMember(userId, groupId)}
              movePending={movePending}
            />
          )}
          </>
        )}
      </Stack>

      {members.length === 0 ? (
        <Blankslate>
          <Blankslate.Heading>No members</Blankslate.Heading>
          <Blankslate.Description>Move people from other groups into this one.</Blankslate.Description>
        </Blankslate>
      ) : (
        <Table.Container className="group-detail-table">
          <DataTable
            data={members}
            columns={[
              {
                header: 'Member',
                field: 'full_name' as const,
                rowHeader: true,
                renderCell: (row: OrgGroupMember) => (
                  <Stack direction="vertical" gap="condensed">
                    <Text weight="semibold">{row.full_name ?? row.email ?? 'Unknown'}</Text>
                    {row.full_name && <Text size="small" className="group-detail-muted">{row.email}</Text>}
                  </Stack>
                ),
              },
              {
                header: 'Added',
                field: 'added_at' as const,
                width: 120,
                renderCell: (row: OrgGroupMember) => (
                  <Text size="small" className="group-detail-muted">
                    {new Date(row.added_at).toLocaleDateString()}
                  </Text>
                ),
              },
              ...(isAdmin
                ? [
                    {
                      id: 'actions',
                      header: '',
                      field: 'id' as const,
                      width: 'auto' as const,
                      renderCell: (row: OrgGroupMember) => (
                        <Stack direction="horizontal" gap="condensed" justify="end">
                          <ActionMenu>
                            <ActionMenu.Button size="small" disabled={otherGroups.length === 0}>
                              Move to
                            </ActionMenu.Button>
                            <ActionMenu.Overlay>
                              <ActionList>
                                {otherGroups.map((g) => (
                                  <ActionList.Item key={g.id} onSelect={() => onMoveMember(row.user_id, g.id)}>
                                    {g.name}
                                  </ActionList.Item>
                                ))}
                              </ActionList>
                            </ActionMenu.Overlay>
                          </ActionMenu>
                          {!isDefault && (
                            <Button size="small" variant="danger" onClick={() => setConfirmRemove(row)}>
                              <TrashIcon /> Remove
                            </Button>
                          )}
                        </Stack>
                      ),
                    },
                  ]
                : []),
            ]}
          />
        </Table.Container>
      )}

      {confirmRemove && (
        <ConfirmationDialog
          title="Remove member"
          confirmButtonContent="Remove"
          cancelButtonContent="Cancel"
          confirmButtonType="danger"
          onClose={(gesture) => {
            const member = confirmRemove
            setConfirmRemove(null)
            if (gesture === 'confirm' && member) {
              removeMutation.mutate(member.user_id)
            }
          }}
        >
          Remove <strong>{confirmRemove.full_name ?? confirmRemove.email}</strong> from this group? They go back to
          the Default group.
        </ConfirmationDialog>
      )}
    </Stack>
  )
}
