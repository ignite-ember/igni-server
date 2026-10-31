import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import {
  Banner,
  Heading,
  PageHeader,
  SegmentedControl,
  SkeletonBox,
  Stack,
  Text,
  CounterLabel,
} from '@primer/react'
import { useState } from 'react'
import { SkeletonText } from '@primer/react/experimental'
import client from '../api/client'
import { AreaChart } from '../components/charts/AreaChart'
import type { UserMe, UsageDataPoint, TeamMemberList } from '../types'
import './DashboardPage.css'

interface UsageResponse {
  data: UsageDataPoint[]
  total_tokens: number
  total_input_tokens: number
  total_output_tokens: number
  total_requests: number
}

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <Stack className="stat-card" padding="normal" gap="condensed">
      <Text size="small" weight="normal" className="stat-card-label">
        {label}
      </Text>
      {/* A figure, not a section — see StatCard. F137. */}
      <Text className="stat-card-value">
        {value}
      </Text>
      {sub && (
        <Text size="small" className="stat-card-sub">{sub}</Text>
      )}
    </Stack>
  )
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return n.toLocaleString()
}

export function DashboardPage() {
  useDocumentTitle('Dashboard', 'Your igni dashboard — usage and team overview.')
  const userQuery = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
  })

  // Two independent scope toggles — flipping the Tokens card shouldn't move the
  // Requests card and vice versa. Non-admins are pinned to ``personal``.
  const isAdmin = userQuery.data?.role === 'admin'
  const [tokenScope, setTokenScope] = useState<'personal' | 'team'>('personal')
  const [requestScope, setRequestScope] = useState<'personal' | 'team'>('personal')
  const effectiveTokenScope = isAdmin ? tokenScope : 'personal'
  const effectiveRequestScope = isAdmin ? requestScope : 'personal'

  // ``keepPreviousData`` keeps the prior chart visible while a scope flip
  // refetches, otherwise both cards momentarily collapse to their empty-state
  // text and the page below jumps up.
  const tokenUsageQuery = useQuery({
    queryKey: ['usage', 30, effectiveTokenScope],
    queryFn: () =>
      client
        .get<UsageResponse>('/portal/usage', { params: { days: 30, scope: effectiveTokenScope } })
        .then((r) => r.data),
    placeholderData: keepPreviousData,
  })

  const requestUsageQuery = useQuery({
    queryKey: ['usage', 30, effectiveRequestScope],
    queryFn: () =>
      client
        .get<UsageResponse>('/portal/usage', { params: { days: 30, scope: effectiveRequestScope } })
        .then((r) => r.data),
    placeholderData: keepPreviousData,
  })

  const teamQuery = useQuery({
    queryKey: ['team-members'],
    queryFn: () => client.get<TeamMemberList>('/portal/team').then((r) => r.data),
  })

  if (userQuery.isLoading || tokenUsageQuery.isLoading) {
    return (
      <Stack gap="spacious">
        <PageHeader>
          <PageHeader.TitleArea>
            <PageHeader.Title as="h1">Dashboard</PageHeader.Title>
          </PageHeader.TitleArea>
          <PageHeader.Description>
            <SkeletonText size="bodyMedium" maxWidth={300} />
          </PageHeader.Description>
        </PageHeader>
        <Stack direction="horizontal" gap="normal" wrap="wrap">
          {Array.from({ length: 3 }).map((_, i) => (
            <Stack key={i} className="stat-card" padding="normal" gap="condensed">
              <SkeletonText size="bodySmall" maxWidth={80} />
              <SkeletonText size="titleMedium" maxWidth={60} />
            </Stack>
          ))}
        </Stack>
        <Stack className="chart-card" padding="normal" gap="normal">
          <SkeletonText size="titleSmall" maxWidth={120} />
          <SkeletonBox height={300} />
        </Stack>
      </Stack>
    )
  }

  const hasError = userQuery.isError || tokenUsageQuery.isError || requestUsageQuery.isError

  const user = userQuery.data
  const tokenUsage = tokenUsageQuery.data
  const requestUsage = requestUsageQuery.data
  const team = teamQuery.data

  const activeMembers = team?.members?.filter((m) => m.status === 'active').length ?? 0

  return (
    <Stack gap="spacious">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.Title as="h1">Dashboard</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text size="medium">
            Welcome{user?.full_name ? `, ${user.full_name}` : ''}. Here is your 30-day overview.
          </Text>
        </PageHeader.Description>
      </PageHeader>

      {hasError && (
        <Banner
          variant="warning"
          title="Some data could not be loaded"
          description="Parts of the dashboard may be incomplete. Try refreshing the page."
        />
      )}

      {/* Key metrics */}
      <Stack direction="horizontal" gap="normal" wrap="wrap">
        <StatCard
          label="Total Tokens"
          value={formatTokens(tokenUsage?.total_tokens ?? 0)}
          sub={`${formatTokens(tokenUsage?.total_input_tokens ?? 0)} in · ${formatTokens(tokenUsage?.total_output_tokens ?? 0)} out`}
        />
        <StatCard
          label="Requests"
          value={requestUsage?.total_requests ?? 0}
        />
        <StatCard
          label="Team Members"
          value={teamQuery.isLoading ? '…' : activeMembers}
        />
      </Stack>

      {/* Token usage chart */}
      <Stack className="chart-card" padding="normal" gap="normal">
        <Stack direction="horizontal" align="center" justify="space-between" gap="condensed">
          <Stack direction="horizontal" align="center" gap="condensed">
            <Heading as="h2" variant="small">Token Usage</Heading>
            <CounterLabel>30d</CounterLabel>
          </Stack>
          {isAdmin && (
            <SegmentedControl aria-label="Token usage scope" size="small">
              <SegmentedControl.Button
                selected={tokenScope === 'personal'}
                onClick={() => setTokenScope('personal')}
              >
                You
              </SegmentedControl.Button>
              <SegmentedControl.Button
                selected={tokenScope === 'team'}
                onClick={() => setTokenScope('team')}
              >
                Team
              </SegmentedControl.Button>
            </SegmentedControl>
          )}
        </Stack>
        {tokenUsage?.data && tokenUsage.data.length > 0 ? (
          <AreaChart
            data={tokenUsage.data}
            height={300}
            dataKey="total_tokens"
            series={[
              {
                label: 'Input',
                color: '#0969da',
                data: tokenUsage.data.map((d) => ({ ...d, total_tokens: d.input_tokens })),
              },
              {
                label: 'Output',
                color: '#1a7f37',
                data: tokenUsage.data.map((d) => ({ ...d, total_tokens: d.output_tokens })),
              },
              // Total surfaces in the hover card only — it would otherwise
              // dominate the y-axis and flatten the Input/Output lines.
              { label: 'Total', color: '#8250df', data: tokenUsage.data, tooltipOnly: true },
            ]}
          />
        ) : (
          <Text size="medium" className="dashboard-subtitle">
            No usage data yet. Start using igni to see your analytics here.
          </Text>
        )}
      </Stack>

      {/* Requests-per-day chart — same scope toggle as the Token Usage card */}
      <Stack className="chart-card" padding="normal" gap="normal">
        <Stack direction="horizontal" align="center" justify="space-between" gap="condensed">
          <Stack direction="horizontal" align="center" gap="condensed">
            <Heading as="h2" variant="small">Requests</Heading>
            <CounterLabel>30d</CounterLabel>
          </Stack>
          {isAdmin && (
            <SegmentedControl aria-label="Requests scope" size="small">
              <SegmentedControl.Button
                selected={requestScope === 'personal'}
                onClick={() => setRequestScope('personal')}
              >
                You
              </SegmentedControl.Button>
              <SegmentedControl.Button
                selected={requestScope === 'team'}
                onClick={() => setRequestScope('team')}
              >
                Team
              </SegmentedControl.Button>
            </SegmentedControl>
          )}
        </Stack>
        {requestUsage?.data && requestUsage.data.length > 0 ? (
          <AreaChart
            data={requestUsage.data}
            height={240}
            dataKey="request_count"
            singleLabel="Requests"
          />
        ) : (
          <Text size="medium" className="dashboard-subtitle">
            No requests yet.
          </Text>
        )}
      </Stack>

    </Stack>
  )
}

