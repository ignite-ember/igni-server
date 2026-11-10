/**
 * The admin tools an auditor, a regulator or a person exercising a right
 * asks for: the audit trail, the access review, subject access and
 * erasure.
 *
 * One page with four tabs, because all four are things an administrator
 * does *about* people rather than to the product, and none is used daily
 * enough to earn its own sidebar entry. The active tab lives in the URL
 * hash, so a runbook link to `#erasure` lands where it says.
 *
 * Every endpoint behind these tabs is admin-only, and the sidebar hides
 * the entry for a member rather than showing a link that would 403.
 */

import { useLocation, useNavigate } from 'react-router-dom'
import { PageHeader, Stack, Text, UnderlineNav } from '@primer/react'
import { ListUnorderedIcon, PeopleIcon, PersonIcon, TrashIcon } from '@primer/octicons-react'

import { AccessReviewTab } from '../components/compliance/AccessReviewTab'
import { AuditTrailTab } from '../components/compliance/AuditTrailTab'
import { ErasureTab } from '../components/compliance/ErasureTab'
import { SubjectAccessTab } from '../components/compliance/SubjectAccessTab'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import './CompliancePage.css'

const TABS = [
  { key: 'audit', label: 'Audit trail', icon: ListUnorderedIcon, Panel: AuditTrailTab },
  { key: 'access-review', label: 'Access review', icon: PeopleIcon, Panel: AccessReviewTab },
  { key: 'subject-access', label: 'Subject access', icon: PersonIcon, Panel: SubjectAccessTab },
  { key: 'erasure', label: 'Erasure', icon: TrashIcon, Panel: ErasureTab },
] as const

type Tab = (typeof TABS)[number]

/** The tab a URL hash names, or the audit trail for anything else. */
function tabOf(hash: string): Tab {
  const key = hash.replace('#', '')
  return TABS.find((t) => t.key === key) ?? TABS[0]
}

export function CompliancePage() {
  useDocumentTitle('Compliance', 'Audit trail, access review, subject access and erasure.')
  const location = useLocation()
  const navigate = useNavigate()

  // Derived from the hash, never stored beside it: one source of truth.
  const tab = tabOf(location.hash)

  return (
    <Stack gap="spacious">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.Title as="h1">Compliance</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text>
            The four things an auditor, a regulator or a person exercising a right will
            ask for. Everything here stays in this deployment.
          </Text>
        </PageHeader.Description>
      </PageHeader>

      {/* `UnderlineNav` handles the keyboard and the current-page semantics. */}
      <UnderlineNav aria-label="Compliance sections">
        {TABS.map((entry) => (
          <UnderlineNav.Item
            key={entry.key}
            icon={entry.icon}
            aria-current={tab.key === entry.key ? 'page' : undefined}
            onSelect={(e) => {
              e.preventDefault()
              navigate(`/compliance#${entry.key}`, { replace: true })
            }}
          >
            {entry.label}
          </UnderlineNav.Item>
        ))}
      </UnderlineNav>

      <tab.Panel />
    </Stack>
  )
}
