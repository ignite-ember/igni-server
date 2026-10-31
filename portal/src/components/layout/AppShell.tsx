import { Outlet, useLocation } from 'react-router-dom'
import { PageLayout } from '@primer/react'
import { useQuery } from '@tanstack/react-query'
import client from '../../api/client'
import type { UserMe } from '../../types'
import { DeploymentBanner } from './DeploymentBanner'
import { LicenceBanner } from './LicenceBanner'
import { NoOrganisation } from './NoOrganisation'
import { Sidebar } from './Sidebar'
import './AppShell.css'

/** Pages that are about the account or the install, not an organisation. */
const WITHOUT_AN_ORGANISATION = ['/settings', '/super-admins', '/organisations']

export function AppShell() {
  const location = useLocation()
  const meQuery = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  })
  const inNoOrganisation = meQuery.data !== undefined && (meQuery.data.organizations ?? []).length === 0
  const pageNeedsOne = !WITHOUT_AN_ORGANISATION.some((path) => location.pathname.startsWith(path))

  return (
    <PageLayout className="app-shell">
      <PageLayout.Pane position="start">
        <Sidebar />
      </PageLayout.Pane>
      <PageLayout.Content>
        <DeploymentBanner isSuperAdmin={meQuery.data?.is_super_admin === true} />
        <LicenceBanner />
        {inNoOrganisation && pageNeedsOne ? (
          <NoOrganisation isSuperAdmin={meQuery.data?.is_super_admin === true} />
        ) : (
          <Outlet />
        )}
      </PageLayout.Content>
    </PageLayout>
  )
}
