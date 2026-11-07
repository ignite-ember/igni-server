import { RelativeTime, Text } from '@primer/react'

import { countOf } from '../../../utils/plural'
import { Facts, Rows, SectionCard } from '../SectionCard'
import type { DashboardData } from '../useDashboardData'

/** Programs registered to act as themselves, each with a VM. Admins only. */
export function AppsSection({ apps }: Pick<DashboardData, 'apps'>) {
  const list = apps.data?.apps ?? []
  const newest = [...list].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 3)
  return (
    <SectionCard
      title="Apps"
      to="/apps"
      linkLabel={`All ${countOf(list.length, 'app')}`}
      loading={apps.isLoading}
      failed={apps.isError}
    >
      <Facts
        facts={[
          { label: 'Apps', value: list.length.toLocaleString() },
          {
            label: 'On their own VM profile',
            value: list.filter((a) => a.vm_profile_id !== null).length.toLocaleString(),
          },
        ]}
      />
      <Rows empty="No apps registered. An app signs in as itself, with its own VM.">
        {newest.map((a) => (
          <li key={a.app_id} className="dashboard-row">
            <Text className="dashboard-row-main">{a.name}</Text>
            <Text size="small" className="dashboard-muted">
              {a.owner_email ?? 'no owner'} · <RelativeTime datetime={a.created_at} />
            </Text>
          </li>
        ))}
      </Rows>
    </SectionCard>
  )
}
