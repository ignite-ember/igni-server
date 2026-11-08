import { NavList } from '@primer/react'
import { Link, useLocation } from 'react-router-dom'

import { useOrg } from '../../auth/useOrg'
import { useMe } from '../../hooks/useMe'
import { OrgMenu } from './OrgMenu'
import { deploymentItems, mainItems, orgItems, type NavItem } from './sidebarNav'
import { resourceItems } from './sidebarResources'
import { UserMenu } from './UserMenu'
import './Sidebar.css'

/**
 * One routed nav group.
 *
 * Each entry is `as={Link}` with a `to`, a real link. They were once
 * `<NavList.Item onClick={() => navigate(...)}>`, which Primer rendered
 * as an `<a>` with no `href`: left-click worked and nothing else did —
 * cmd-click navigated the current tab, and middle-click, "open in new
 * tab" and "copy link address" were dead. React Router's `Link` ignores
 * modified clicks and lets the browser do what the user asked.
 */
function NavGroup({ title, items }: { title: string; items: NavItem[] }) {
  const { pathname } = useLocation()

  return (
    <NavList.Group title={title}>
      {items.map((item) => {
        const href = `/${item.path}`
        const current = pathname === href || pathname.startsWith(`${href}/`)
        return (
          <NavList.Item key={item.path} as={Link} to={href} aria-current={current ? 'page' : undefined}>
            <NavList.LeadingVisual>
              <item.icon />
            </NavList.LeadingVisual>
            {item.label}
          </NavList.Item>
        )
      })}
    </NavList.Group>
  )
}

export function Sidebar() {
  const { activeOrg } = useOrg()
  const meQuery = useMe()
  const isSuperAdmin = meQuery.data?.is_super_admin === true
  const isOrgAdmin = activeOrg?.role === 'admin'

  return (
    <div className="sidebar-root">
      {/* Combined org + user identity card */}
      <div className="sidebar-identity">
        <OrgMenu isSuperAdmin={isSuperAdmin} />
        <hr className="sidebar-identity-divider" />
        <UserMenu me={meQuery.data} isLoading={meQuery.isLoading} />
      </div>

      <NavList aria-label="Main navigation">
        <NavGroup title="Overview" items={mainItems} />
        <NavGroup title="Organization" items={orgItems.filter((item) => !item.adminOnly || isOrgAdmin)} />
        {isSuperAdmin && <NavGroup title="Deployment" items={deploymentItems} />}

        <NavList.Group title="Resources">
          {/* Published on the public site, not routed here — a plain
          link out rather than a router `Link`. */}
          {resourceItems.map((item) => (
            <NavList.Item key={item.href} href={item.href}>
              <NavList.LeadingVisual>
                <item.icon />
              </NavList.LeadingVisual>
              {item.label}
            </NavList.Item>
          ))}
        </NavList.Group>
      </NavList>
    </div>
  )
}
