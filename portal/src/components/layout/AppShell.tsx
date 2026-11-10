import { Outlet, useLocation } from 'react-router-dom'
import { PageLayout } from '@primer/react'
import { useMe } from '../../hooks/useMe'
import { DeploymentBanner } from './DeploymentBanner'
import { LicenceBanner } from './LicenceBanner'
import { NoOrganisation } from './NoOrganisation'
import { Sidebar } from './Sidebar'
import './AppShell.css'

/** Pages that are about the account or the install, not an organisation. */
const WITHOUT_AN_ORGANISATION = ['/settings', '/super-admins', '/organisations', '/licence']

export function AppShell() {
  const location = useLocation()
  const meQuery = useMe()
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
