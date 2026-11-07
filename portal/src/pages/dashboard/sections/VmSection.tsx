import { Label, Text } from '@primer/react'

import { countOf } from '../../../utils/plural'
import { formatMemory } from '../format'
import { Facts, Rows, SectionCard } from '../SectionCard'
import type { DashboardData } from '../useDashboardData'

/** What each producer's microVM gets, and the organisation's allowance. Admins only. */
export function VmSection({ vmProfiles }: Pick<DashboardData, 'vmProfiles'>) {
  const overview = vmProfiles.data
  const profiles = overview?.profiles ?? []
  const hours = overview?.quota.awake_vm_seconds_per_month
  const assigned = (id: string) =>
    (overview?.groups.filter((g) => (g.vm_profile_id ?? overview.default_profile_id) === id).length ?? 0) +
    (overview?.apps.filter((a) => (a.vm_profile_id ?? overview.default_profile_id) === id).length ?? 0)
  return (
    <SectionCard
      title="VM profiles"
      to="/vm-profiles"
      linkLabel={`All ${countOf(profiles.length, 'profile')}`}
      loading={vmProfiles.isLoading}
      failed={vmProfiles.isError}
    >
      <Facts
        facts={[
          { label: 'Profiles', value: profiles.length.toLocaleString() },
          {
            label: 'Awake time a month',
            value: hours == null ? 'Unlimited' : `${Math.round(hours / 3600).toLocaleString()} h`,
          },
          { label: 'Quota', value: overview?.quota.enforced ? 'Enforced' : 'Not enforced' },
        ]}
      />
      <Rows empty="No VM profiles yet.">
        {profiles.slice(0, 3).map((p) => (
          <li key={p.id} className="dashboard-row">
            <Text className="dashboard-row-main">
              {p.name} {p.is_default && <Label variant="secondary">Default</Label>}{' '}
              {!p.within_caps && <Label variant="danger">Over caps</Label>}
            </Text>
            <Text size="small" className="dashboard-muted">
              {formatMemory(p.memory_mib)} · {countOf(assigned(p.id), 'group or app', 'groups and apps')}
            </Text>
          </li>
        ))}
      </Rows>
    </SectionCard>
  )
}
