import { useState } from 'react'
import { Banner, PageHeader, Stack, Text } from '@primer/react'
import { SkeletonText } from '@primer/react/experimental'

import { useDocumentTitle } from '../hooks/useDocumentTitle'
import { AttentionPanel } from './dashboard/AttentionPanel'
import { attentionItems } from './dashboard/attention'
import { HeadlineStats } from './dashboard/HeadlineStats'
import { AccessSection } from './dashboard/sections/AccessSection'
import { AppsSection } from './dashboard/sections/AppsSection'
import { CommandsSection } from './dashboard/sections/CommandsSection'
import { ComplianceSection } from './dashboard/sections/ComplianceSection'
import { GroupsSection } from './dashboard/sections/GroupsSection'
import { InferenceSection } from './dashboard/sections/InferenceSection'
import { TeamSection } from './dashboard/sections/TeamSection'
import { VmSection } from './dashboard/sections/VmSection'
import type { UsageScope } from './dashboard/types'
import { UsageCharts } from './dashboard/UsageCharts'
import { useDashboardData } from './dashboard/useDashboardData'
import './dashboard/dashboard.css'

/**
 * The portal's home: igni at a glance.
 *
 * What needs attention first, then the numbers that summarise it, the
 * month's usage, and a card for every area of the system with its few
 * facts that matter and its latest things. A member sees the areas that
 * are theirs; an admin the whole organisation (the sidebar's rules).
 */
export function DashboardPage() {
  useDocumentTitle('Dashboard', 'Your igni at a glance — what needs attention, usage, and every area of the system.')
  const [tokenScope, setTokenScope] = useState<UsageScope>('personal')
  const [requestScope, setRequestScope] = useState<UsageScope>('personal')
  const data = useDashboardData(tokenScope, requestScope)
  const { me, isAdmin } = data

  if (me.isLoading) {
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
      </Stack>
    )
  }

  const user = me.data
  const attention = attentionItems({
    team: data.team.data,
    inference: data.inference.data,
    vmProfiles: data.vmProfiles.data,
    erasures: data.erasures.data,
    audit: data.audit.data,
    apiKeys: data.apiKeys.data,
    vmCredentials: data.vmCredentials.data,
    commands: data.commands.data,
  })
  // The panel waits for what it reads, so "nothing needs attention" is
  // never said about data that has not arrived.
  const attentionReady = [
    data.apiKeys,
    data.vmCredentials,
    data.commands,
    ...(isAdmin ? [data.team, data.inference, data.vmProfiles, data.erasures, data.audit] : []),
  ].every((q) => !q.isLoading)
  const usageFailed = me.isError || data.tokenUsage.isError || data.requestUsage.isError
  const where = user?.org_display_name ? ` Here is ${user.org_display_name} at a glance.` : ''

  return (
    <Stack gap="spacious">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.Title as="h1">Dashboard</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text size="medium">
            Welcome{user?.full_name ? `, ${user.full_name}` : ''}.{where}
          </Text>
        </PageHeader.Description>
      </PageHeader>

      {usageFailed && (
        <Banner
          variant="warning"
          title="Some data could not be loaded"
          description="Parts of the dashboard may be incomplete. Try refreshing the page."
        />
      )}

      <AttentionPanel items={attention} ready={attentionReady} />

      <HeadlineStats data={data} />

      <UsageCharts
        isAdmin={isAdmin}
        tokens={data.tokenUsage.data}
        requests={data.requestUsage.data}
        tokenScope={tokenScope}
        requestScope={requestScope}
        onTokenScope={setTokenScope}
        onRequestScope={setRequestScope}
      />

      <div className="dashboard-grid">
        {isAdmin && <TeamSection team={data.team} />}
        <GroupsSection groups={data.groups} />
        {isAdmin && <InferenceSection inference={data.inference} />}
        <CommandsSection commands={data.commands} />
        <AccessSection apiKeys={data.apiKeys} vmCredentials={data.vmCredentials} />
        {isAdmin && <VmSection vmProfiles={data.vmProfiles} />}
        {isAdmin && <AppsSection apps={data.apps} />}
        {isAdmin && <ComplianceSection audit={data.audit} erasures={data.erasures} />}
      </div>
    </Stack>
  )
}
