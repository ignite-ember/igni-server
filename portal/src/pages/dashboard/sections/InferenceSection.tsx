import { ProgressBar, Text } from '@primer/react'

import { countOf } from '../../../utils/plural'
import { distinctNodes } from '../attention'
import { formatPercent } from '../format'
import { Facts, Rows, SectionCard } from '../SectionCard'
import type { DashboardData } from '../useDashboardData'

const POOL_NAME = { chat: 'Chat', codeindex: 'CodeIndex' } as const

/** The GPUs answering requests: how many, how well, how busy. Admins only. */
export function InferenceSection({ inference }: Pick<DashboardData, 'inference'>) {
  const status = inference.data
  const nodes = status ? distinctNodes(status) : []
  const healthy = nodes.filter((n) => !n.drain && n.health.state === 'healthy').length
  const models = new Set(nodes.flatMap((n) => n.models)).size
  return (
    <SectionCard
      title="Inference"
      to="/inference"
      linkLabel={`All ${countOf(nodes.length, 'node')}`}
      loading={inference.isLoading}
      failed={inference.isError}
    >
      <Facts
        facts={[
          {
            label: 'Healthy nodes',
            value: `${healthy} of ${nodes.length}`,
            tone: nodes.length > 0 && healthy < nodes.length ? 'bad' : undefined,
          },
          { label: 'Models', value: models.toLocaleString() },
        ]}
      />
      <Rows empty="No pool has a node yet.">
        {(status?.pools ?? [])
          .filter((p) => p.nodes.length > 0)
          .map((p) => (
            <li key={p.pool} className="dashboard-row dashboard-row--stacked">
              <Text className="dashboard-row-main">
                {POOL_NAME[p.pool]}
                <Text size="small" className="dashboard-muted">
                  {' '}
                  {p.total_in_flight} of {p.total_capacity} in flight · {formatPercent(p.utilization)}
                </Text>
              </Text>
              <ProgressBar
                progress={Math.round(p.utilization * 100)}
                aria-label={`${POOL_NAME[p.pool]} pool utilisation`}
                barSize="small"
              />
            </li>
          ))}
      </Rows>
    </SectionCard>
  )
}
