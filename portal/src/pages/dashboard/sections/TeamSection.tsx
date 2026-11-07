import { RelativeTime, Text } from '@primer/react'

import type { TeamMember } from '../../../types'
import { countOf } from '../../../utils/plural'
import { formatTokens } from '../format'
import { Facts, Rows, SectionCard } from '../SectionCard'
import type { DashboardData } from '../useDashboardData'

/** Who is in the organisation, and who uses igni most. Admins only. */
export function TeamSection({ team }: Pick<DashboardData, 'team'>) {
  const members = team.data?.members ?? []
  const count = (status: TeamMember['status']) => members.filter((m) => m.status === status).length
  const busiest = [...members]
    .filter((m) => m.status === 'active')
    .sort((a, b) => b.total_tokens - a.total_tokens)
    .slice(0, 3)
  return (
    <SectionCard
      title="Team"
      to="/team"
      linkLabel={`All ${countOf(members.length, 'person', 'people')}`}
      loading={team.isLoading}
      failed={team.isError}
    >
      <Facts
        facts={[
          { label: 'Active', value: count('active').toLocaleString() },
          { label: 'Invited', value: count('invited').toLocaleString() },
          { label: 'Deactivated', value: count('deactivated').toLocaleString() },
        ]}
      />
      <Rows empty="Nobody has used igni yet.">
        {busiest.map((m) => (
          <li key={m.id} className="dashboard-row">
            <Text className="dashboard-row-main">{m.full_name ?? m.email}</Text>
            <Text size="small" className="dashboard-muted">
              {formatTokens(m.total_tokens)} tokens
              {m.last_active && (
                <>
                  {' · '}
                  <RelativeTime datetime={m.last_active} />
                </>
              )}
            </Text>
          </li>
        ))}
      </Rows>
    </SectionCard>
  )
}
