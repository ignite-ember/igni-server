import { useCallback, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { Banner, PageHeader, Stack, Text } from '@primer/react'
import { Blankslate, SkeletonText } from '@primer/react/experimental'
import { AppsIcon } from '@primer/octicons-react'
import client from '../api/client'
import { AppCloudCredential } from '../components/app/AppCloudCredential'
import { AppExtensions } from '../components/app/AppExtensions'
import { AppLegalHold } from '../components/app/AppLegalHold'
import { AppVmProfile } from '../components/app/AppVmProfile'
import { DiscardApp } from '../components/app/DiscardApp'
import type { Feedback, FeedbackVariant } from '../components/app/feedback'
import { ReplaceAppKeys } from '../components/app/ReplaceAppKeys'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import { useMe } from '../hooks/useMe'
import type { OrgApp } from '../types'

/**
 * One app: its own list of extensions (off until switched on here, and
 * nothing to do with any group's), its VM profile, a key rotation, a
 * legal hold, and discarding it — removed at once, its place under the
 * licence freed, its VM erased in the background.
 */
export function AppDetailPage() {
  const { appId = '' } = useParams<{ appId: string }>()
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const onFeedback = useCallback((variant: FeedbackVariant, message: string) => setFeedback({ variant, message }), [])

  const me = useMe()
  const app = useQuery({
    queryKey: ['app', appId],
    queryFn: () => client.get<OrgApp>(`/portal/apps/${appId}`).then((r) => r.data),
  })
  useDocumentTitle(app.data?.name ?? 'App')
  const orgId = me.data?.org_id ?? ''

  if (app.isError) {
    return (
      <Blankslate>
        <Blankslate.Heading>App not found</Blankslate.Heading>
        <Blankslate.Description>It may have been removed, or it belongs to another organisation.</Blankslate.Description>
      </Blankslate>
    )
  }
  if (!app.data) return <SkeletonText size="bodyMedium" maxWidth={300} />

  return (
    <Stack gap="spacious">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.LeadingVisual>
            <AppsIcon />
          </PageHeader.LeadingVisual>
          <PageHeader.Title>{app.data.name}</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text>
            Client id <code>{app.data.client_id}</code> · owned by {app.data.owner_email ?? 'nobody'} · key{' '}
            <code>{app.data.thumbprint}</code>
          </Text>
        </PageHeader.Description>
      </PageHeader>

      {feedback && (
        <Banner
          variant={feedback.variant}
          title={feedback.variant === 'success' ? 'Saved' : 'Not changed'}
          description={feedback.message}
          onDismiss={() => setFeedback(null)}
        />
      )}

      <AppExtensions appId={appId} onFeedback={onFeedback} />
      <AppVmProfile app={app.data} orgId={orgId} onFeedback={onFeedback} />
      <ReplaceAppKeys appId={appId} onFeedback={onFeedback} />
      <AppCloudCredential appId={appId} onFeedback={onFeedback} />
      <AppLegalHold appId={appId} orgId={orgId} onFeedback={onFeedback} />
      <DiscardApp app={app.data} orgId={orgId} onFeedback={onFeedback} />
    </Stack>
  )
}
