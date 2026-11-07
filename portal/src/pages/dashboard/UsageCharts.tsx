import type { ReactNode } from 'react'
import { CounterLabel, Heading, SegmentedControl, Stack, Text } from '@primer/react'

import { AreaChart } from '../../components/charts/AreaChart'
import type { UsageResponse, UsageScope } from './types'

/** You or the whole team, for an admin; a member only ever sees their own. */
function ScopeToggle({ label, scope, onChange }: { label: string; scope: UsageScope; onChange: (s: UsageScope) => void }) {
  return (
    <SegmentedControl aria-label={label} size="small">
      <SegmentedControl.Button selected={scope === 'personal'} onClick={() => onChange('personal')}>
        You
      </SegmentedControl.Button>
      <SegmentedControl.Button selected={scope === 'team'} onClick={() => onChange('team')}>
        Team
      </SegmentedControl.Button>
    </SegmentedControl>
  )
}

function ChartCard({
  title,
  toggle,
  children,
}: {
  title: string
  toggle: ReactNode
  children: ReactNode
}) {
  return (
    <Stack className="chart-card" padding="normal" gap="normal">
      <Stack direction="horizontal" align="center" justify="space-between" gap="condensed">
        <Stack direction="horizontal" align="center" gap="condensed">
          <Heading as="h2" variant="small">
            {title}
          </Heading>
          <CounterLabel>30d</CounterLabel>
        </Stack>
        {toggle}
      </Stack>
      {children}
    </Stack>
  )
}

/**
 * Tokens and requests over 30 days, side by side. Two independent scope
 * toggles: flipping one chart must not move the other.
 */
export function UsageCharts({
  isAdmin,
  tokens,
  requests,
  tokenScope,
  requestScope,
  onTokenScope,
  onRequestScope,
}: {
  isAdmin: boolean
  tokens?: UsageResponse
  requests?: UsageResponse
  tokenScope: UsageScope
  requestScope: UsageScope
  onTokenScope: (s: UsageScope) => void
  onRequestScope: (s: UsageScope) => void
}) {
  return (
    <div className="dashboard-charts">
      <ChartCard
        title="Token usage"
        toggle={isAdmin && <ScopeToggle label="Token usage scope" scope={tokenScope} onChange={onTokenScope} />}
      >
        {tokens?.data.length ? (
          <AreaChart
            data={tokens.data}
            height={240}
            dataKey="total_tokens"
            series={[
              {
                label: 'Input',
                color: '#0969da',
                data: tokens.data.map((d) => ({ ...d, total_tokens: d.input_tokens })),
              },
              {
                label: 'Output',
                color: '#1a7f37',
                data: tokens.data.map((d) => ({ ...d, total_tokens: d.output_tokens })),
              },
              // Total surfaces in the hover card only — it would otherwise
              // dominate the y-axis and flatten the Input/Output lines.
              { label: 'Total', color: '#8250df', data: tokens.data, tooltipOnly: true },
            ]}
          />
        ) : (
          <Text size="medium" className="dashboard-muted">
            No usage data yet. Start using igni to see your analytics here.
          </Text>
        )}
      </ChartCard>
      <ChartCard
        title="Requests"
        toggle={isAdmin && <ScopeToggle label="Requests scope" scope={requestScope} onChange={onRequestScope} />}
      >
        {requests?.data.length ? (
          <AreaChart data={requests.data} height={240} dataKey="request_count" singleLabel="Requests" />
        ) : (
          <Text size="medium" className="dashboard-muted">
            No requests yet.
          </Text>
        )}
      </ChartCard>
    </div>
  )
}
