import type { HTMLAttributes } from 'react'
import { RelativeTime, Stack, Text, VisuallyHidden } from '@primer/react'
import { DataTable, Table } from '@primer/react/experimental'
import type { Column } from '@primer/react/experimental'

import type { TeamMember } from '../../types'
import { formatTokens } from '../../utils/formatTokens'
import { MemberActions } from './MemberActions'
import type { MemberActionHandlers } from './MemberActions'
import { RoleBadge } from './RoleBadge'
import { StatusBadge } from './StatusBadge'

const COLUMNS: Column<TeamMember>[] = [
  {
    header: 'Member',
    field: 'full_name',
    rowHeader: true,
    renderCell: (row) => (
      <Stack gap="none">
        <Text weight="semibold">{row.full_name ?? row.email}</Text>
        {row.full_name && (
          <Text size="small" className="team-muted">
            {row.email}
          </Text>
        )}
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
    // Requests, not sessions: each chat request is a row in chat usage,
    // and nothing records a session.
    header: 'Requests (30d)',
    field: 'total_requests',
    align: 'end',
    renderCell: (row) => <Text>{row.total_requests > 0 ? row.total_requests : '—'}</Text>,
  },
  {
    header: 'Tokens (30d)',
    field: 'total_tokens',
    align: 'end',
    renderCell: (row) => <Text>{row.total_tokens > 0 ? formatTokens(row.total_tokens) : '—'}</Text>,
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
]

/**
 * Table.Title's heading element. Primer types `as` against the global JSX
 * namespace, which React 19 no longer declares, so a tag name does not
 * type-check there; a component does.
 */
function MembersHeading(props: HTMLAttributes<HTMLHeadingElement>) {
  return <h3 {...props} />
}

function actionsColumn(selfId: string | undefined, handlers: MemberActionHandlers): Column<TeamMember> {
  return {
    id: 'actions',
    // Named, not empty. A column header with no text is a cell a screen
    // reader cannot describe — axe's empty-table-header. F137.
    header: () => <VisuallyHidden>Actions</VisuallyHidden>,
    field: 'id',
    width: 'auto',
    renderCell: (row) => <MemberActions member={row} isSelf={row.id === selfId} handlers={handlers} />,
  }
}

/**
 * The member list. `handlers` is null for a viewer who is not an admin,
 * who gets no actions column at all.
 */
export function MembersTable({
  members,
  selfId,
  handlers,
}: {
  members: TeamMember[]
  selfId: string | undefined
  handlers: MemberActionHandlers | null
}) {
  const columns = handlers ? [...COLUMNS, actionsColumn(selfId, handlers)] : COLUMNS
  return (
    <Table.Container className="team-table">
      <Table.Title as={MembersHeading} id="team-title">
        Members
      </Table.Title>
      <DataTable aria-labelledby="team-title" data={members} columns={columns} />
    </Table.Container>
  )
}
