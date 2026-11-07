import { Link as RouterLink } from 'react-router-dom'

import { StatCard } from '../../components/StatCard'
import { distinctNodes } from './attention'
import { formatTokens } from './format'
import type { DashboardData } from './useDashboardData'

/** One stat card that is also the way to the page behind it. */
function LinkedStat({ to, ...card }: { to: string; label: string; value: string; sub?: string; tone?: 'bad' }) {
  return (
    <RouterLink to={to} className="dashboard-stat-link" aria-label={`${card.label}: ${card.value}`}>
      <StatCard {...card} />
    </RouterLink>
  )
}

const pending = '…'

/** The numbers that summarise igni for this person; each but usage is a link to its page. */
export function HeadlineStats({ data }: { data: DashboardData }) {
  const { isAdmin, tokenUsage, requestUsage, team, groups, apps, inference, apiKeys } = data
  const tokens = tokenUsage.data
  const members = team.data?.members ?? []
  const nodes = inference.data ? distinctNodes(inference.data) : []
  const healthy = nodes.filter((n) => !n.drain && n.health.state === 'healthy').length
  return (
    <div className="dashboard-stats">
      {/* Not links: what they summarise is the charts just below. */}
      <StatCard
        label="Tokens · 30 days"
        value={formatTokens(tokens?.total_tokens ?? 0)}
        sub={`${formatTokens(tokens?.total_input_tokens ?? 0)} in · ${formatTokens(tokens?.total_output_tokens ?? 0)} out`}
      />
      <StatCard label="Requests · 30 days" value={(requestUsage.data?.total_requests ?? 0).toLocaleString()} />
      {isAdmin && (
        <LinkedStat
          to="/team"
          label="People"
          value={team.isLoading ? pending : members.filter((m) => m.status === 'active').length.toLocaleString()}
          sub={`${members.filter((m) => m.status === 'invited').length} invited`}
        />
      )}
      <LinkedStat
        to="/groups"
        label="Groups"
        value={groups.isLoading ? pending : (groups.data?.total ?? 0).toLocaleString()}
      />
      {isAdmin && (
        <LinkedStat to="/apps" label="Apps" value={apps.isLoading ? pending : (apps.data?.apps.length ?? 0).toLocaleString()} />
      )}
      {isAdmin ? (
        <LinkedStat
          to="/inference"
          label="Inference nodes"
          value={inference.isLoading ? pending : `${healthy} / ${nodes.length}`}
          sub="healthy"
          tone={nodes.length > 0 && healthy < nodes.length ? 'bad' : undefined}
        />
      ) : (
        <LinkedStat
          to="/api-keys"
          label="API keys"
          value={apiKeys.isLoading ? pending : (apiKeys.data?.filter((k) => !k.expired).length ?? 0).toLocaleString()}
          sub="in use"
        />
      )}
    </div>
  )
}
