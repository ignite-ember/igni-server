import { Text } from '@primer/react'

import { KEY_EXPIRY_WARNING_DAYS } from '../attention'
import { daysUntil } from '../format'
import { Facts, Rows, SectionCard } from '../SectionCard'
import type { DashboardData } from '../useDashboardData'

/**
 * How programs and VMs sign in: personal API keys, and each VM's own
 * credential. A member sees their own; an admin everyone's.
 */
export function AccessSection({ apiKeys, vmCredentials }: Pick<DashboardData, 'apiKeys' | 'vmCredentials'>) {
  const keys = apiKeys.data ?? []
  const live = keys.filter((k) => !k.expired)
  const expiring = live.filter((k) => daysUntil(k.expires_at) <= KEY_EXPIRY_WARNING_DAYS)
  const credentials = vmCredentials.data ?? []
  const soonest = [...live].sort((a, b) => a.expires_at.localeCompare(b.expires_at)).slice(0, 3)
  return (
    <SectionCard
      title="Access"
      to="/api-keys"
      linkLabel="API keys"
      loading={apiKeys.isLoading || vmCredentials.isLoading}
      failed={apiKeys.isError && vmCredentials.isError}
    >
      <Facts
        facts={[
          { label: 'API keys in use', value: live.length.toLocaleString() },
          {
            label: `Expiring in ${KEY_EXPIRY_WARNING_DAYS} days`,
            value: expiring.length.toLocaleString(),
            tone: expiring.length ? 'bad' : undefined,
          },
          { label: 'VM credentials', value: credentials.length.toLocaleString() },
        ]}
      />
      <Rows empty="No API keys. Make one to let a program act as you.">
        {soonest.map((k) => {
          const days = daysUntil(k.expires_at)
          return (
            <li key={k.key_id} className="dashboard-row">
              <Text className="dashboard-row-main">{k.name}</Text>
              <Text size="small" className={days <= KEY_EXPIRY_WARNING_DAYS ? 'dashboard-bad' : 'dashboard-muted'}>
                {days <= 0 ? 'expires today' : `expires in ${days} ${days === 1 ? 'day' : 'days'}`}
              </Text>
            </li>
          )
        })}
      </Rows>
    </SectionCard>
  )
}
