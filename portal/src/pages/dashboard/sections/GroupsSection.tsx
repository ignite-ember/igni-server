import { Label, Text } from '@primer/react'

import { countOf } from '../../../utils/plural'
import { Facts, Rows, SectionCard } from '../SectionCard'
import type { DashboardData } from '../useDashboardData'

/** The groups people are in, which decide what their igni ships with. */
export function GroupsSection({ groups }: Pick<DashboardData, 'groups'>) {
  const list = groups.data?.groups ?? []
  const largest = [...list].sort((a, b) => b.member_count - a.member_count).slice(0, 3)
  const entries = list.reduce((sum, g) => sum + g.entry_count, 0)
  return (
    <SectionCard
      title="Groups"
      to="/groups"
      linkLabel={`All ${countOf(groups.data?.total ?? list.length, 'group')}`}
      loading={groups.isLoading}
      failed={groups.isError}
    >
      <Facts
        facts={[
          { label: 'Groups', value: (groups.data?.total ?? list.length).toLocaleString() },
          { label: 'Agents, MCPs & settings', value: entries.toLocaleString() },
        ]}
      />
      <Rows empty="No groups yet.">
        {largest.map((g) => (
          <li key={g.id} className="dashboard-row">
            <Text className="dashboard-row-main">
              {g.name} {g.is_default && <Label variant="secondary">Default</Label>}
            </Text>
            <Text size="small" className="dashboard-muted">
              {countOf(g.member_count, 'member')} · {countOf(g.entry_count, 'entry', 'entries')}
            </Text>
          </li>
        ))}
      </Rows>
    </SectionCard>
  )
}
