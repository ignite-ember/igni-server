import { Label, RelativeTime, Text } from '@primer/react'

import type { ErasureState } from '../../../types'
import { Facts, Rows, SectionCard } from '../SectionCard'
import type { DashboardData } from '../useDashboardData'

const OUTCOME: Record<string, 'success' | 'attention' | 'danger' | 'secondary'> = {
  success: 'success',
  denied: 'attention',
  error: 'danger',
}

/** The audit trail's latest entries, and VMs on their way to erasure. Admins only. */
export function ComplianceSection({ audit, erasures }: Pick<DashboardData, 'audit' | 'erasures'>) {
  const events = audit.data?.events ?? []
  const pending = erasures.data?.erasures ?? []
  const inState = (state: ErasureState) => pending.filter((e) => e.state === state).length
  return (
    <SectionCard
      title="Compliance"
      to="/compliance"
      linkLabel="The audit trail"
      loading={audit.isLoading || erasures.isLoading}
      failed={audit.isError && erasures.isError}
    >
      <Facts
        facts={[
          { label: 'Erasures scheduled', value: inState('scheduled').toLocaleString() },
          { label: 'On legal hold', value: pending.filter((e) => e.legal_hold).length.toLocaleString() },
          { label: 'Erased', value: inState('erased').toLocaleString() },
        ]}
      />
      <Rows empty="Nothing recorded yet.">
        {events.map((e) => (
          <li key={e.id} className="dashboard-row">
            <Text className="dashboard-row-main dashboard-code" title={`${e.method} ${e.route}`}>
              {e.method} {e.route}
            </Text>
            <Text size="small" className="dashboard-muted dashboard-row-meta">
              <Label variant={OUTCOME[e.outcome] ?? 'secondary'}>{e.outcome}</Label>{' '}
              {e.actor_email ?? 'system'} · <RelativeTime datetime={e.occurred_at} />
            </Text>
          </li>
        ))}
      </Rows>
    </SectionCard>
  )
}
