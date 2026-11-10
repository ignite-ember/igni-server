import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Banner, Breadcrumbs, Button, Label, PageHeader, Stack, UnderlineNav } from '@primer/react'
import { SkeletonText } from '@primer/react/experimental'
import { ArrowLeftIcon, CopyIcon } from '@primer/octicons-react'

import client from '../api/client'
import { refusalOf } from '../api/refusal'
import { CopyGroupDialog } from '../components/group/CopyGroupDialog'
import { EntriesTab } from '../components/group/EntriesTab'
import { ExtensionsTab } from '../components/group/ExtensionsTab'
import { invalidateMembership } from '../components/group/membership'
import { MembersTab } from '../components/group/MembersTab'
import { SettingsTab } from '../components/group/SettingsTab'
import { useFeedbackBanner } from '../components/group/useFeedbackBanner'
import { useMe } from '../hooks/useMe'
import type { OrgGroupDetail } from '../types'
import './cards.css'
import './GroupDetailPage.css'

const TABS = ['settings', 'members', 'entries', 'extensions'] as const

type Tab = (typeof TABS)[number]

/** The tab a `?tab=` value names, or General when it names none of them. */
function tabOf(param: string | null): Tab {
  return TABS.find((tab) => tab === param) ?? 'settings'
}

export function GroupDetailPage() {
  const { id: groupId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const meQuery = useMe()

  const groupQuery = useQuery({
    queryKey: ['group', groupId],
    queryFn: () => client.get<OrgGroupDetail>(`/portal/groups/${groupId}`).then((r) => r.data),
  })

  const isAdmin = meQuery.data?.role === 'admin'
  const group = groupQuery.data

  const [searchParams, setSearchParams] = useSearchParams()
  const activeTab = tabOf(searchParams.get('tab'))
  const setActiveTab = (tab: Tab) => {
    const next = new URLSearchParams(searchParams)
    if (tab === 'settings') {
      next.delete('tab')
    } else {
      next.set('tab', tab)
    }
    setSearchParams(next, { replace: true })
  }
  const feedback = useFeedbackBanner()
  const showFeedback = feedback.show

  const moveMemberMutation = useMutation({
    mutationFn: ({ userId, toGroupId }: { userId: string; toGroupId: string }) =>
      client.post(`/portal/groups/${toGroupId}/members`, { user_id: userId }),
    onSuccess: (_data, { toGroupId }) => {
      invalidateMembership(queryClient)
      showFeedback('success', toGroupId === groupId ? 'Member moved to this group' : 'Member moved out of this group')
    },
    onError: (err: unknown) => showFeedback('critical', refusalOf(err, 'Failed to move member')),
  })

  const [copying, setCopying] = useState(false)
  const copyButtonRef = useRef<HTMLButtonElement>(null)

  if (groupQuery.isLoading) {
    return (
      <Stack gap="spacious">
        <PageHeader>
          <PageHeader.TitleArea>
            <PageHeader.Title as="h1"><SkeletonText size="bodyMedium" maxWidth={120} /></PageHeader.Title>
          </PageHeader.TitleArea>
        </PageHeader>
        <SkeletonText size="bodyMedium" maxWidth={300} />
      </Stack>
    )
  }

  if (groupQuery.isError || !group) {
    return (
      <Stack gap="spacious">
        <Banner variant="critical" title="Group not found" description="This group may have been deleted." />
        <Button onClick={() => navigate('/groups')}><ArrowLeftIcon /> Back to groups</Button>
      </Stack>
    )
  }

  return (
    <Stack gap="spacious">
      <Breadcrumbs>
        <Breadcrumbs.Item href="/groups">Groups</Breadcrumbs.Item>
        <Breadcrumbs.Item selected>{group.name}</Breadcrumbs.Item>
      </Breadcrumbs>

      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.Title as="h1">{group.name}</PageHeader.Title>
          {/* TrailingVisual, which Primer defines as the slot for
              auxiliary information like status — not Actions, where a
              bare label floated unattached to anything, and not
              Description, which is a line of prose and was carrying a
              whole paragraph the General tab already shows. */}
          <PageHeader.TrailingVisual>
            <Stack direction="horizontal" gap="condensed" align="center">
              {group.is_default && <Label variant="secondary">What igni ships</Label>}
              {group.default_model && <Label variant="accent">{group.default_model}</Label>}
            </Stack>
          </PageHeader.TrailingVisual>
        </PageHeader.TitleArea>
        {isAdmin && (
          <PageHeader.Actions>
            <Button ref={copyButtonRef} leadingVisual={CopyIcon} onClick={() => setCopying(true)}>
              Copy group
            </Button>
          </PageHeader.Actions>
        )}
        <PageHeader.Navigation>
          <UnderlineNav aria-label="Group sections">
            <UnderlineNav.Item aria-current={activeTab === 'settings' ? 'page' : undefined} onClick={() => setActiveTab('settings')}>
              General
            </UnderlineNav.Item>
            {/* ``counter``, not a Label in the children. Primer reserves
                the width the label takes when selected and bold, using
                ``content: attr(data-content)`` — and an array of
                children stringified to "Members ,[object Object]",
                which it then measured. Two tabs sat a hundred pixels
                wider than their text. */}
            <UnderlineNav.Item
              aria-current={activeTab === 'members' ? 'page' : undefined}
              counter={group.member_count > 0 ? group.member_count : undefined}
              onClick={() => setActiveTab('members')}
            >
              Members
            </UnderlineNav.Item>
            <UnderlineNav.Item
              aria-current={activeTab === 'entries' ? 'page' : undefined}
              counter={group.entry_count > 0 ? group.entry_count : undefined}
              onClick={() => setActiveTab('entries')}
            >
              Configuration
            </UnderlineNav.Item>
            <UnderlineNav.Item
              aria-current={activeTab === 'extensions' ? 'page' : undefined}
              onClick={() => setActiveTab('extensions')}
            >
              Extensions
            </UnderlineNav.Item>
          </UnderlineNav>
        </PageHeader.Navigation>
      </PageHeader>

      {copying && (
        <CopyGroupDialog
          group={group}
          returnFocusRef={copyButtonRef}
          onClose={() => setCopying(false)}
          onCopied={(copy) => {
            setCopying(false)
            queryClient.invalidateQueries({ queryKey: ['groups'] })
            navigate(`/groups/${copy.id}`)
          }}
        />
      )}

      {feedback.banner && (
        <Banner variant={feedback.banner.variant} title={feedback.banner.message} onDismiss={feedback.dismiss} />
      )}

      <MembersTab
        groupId={group.id}
        isAdmin={isAdmin}
        isDefault={group.is_default}
        onMoveMember={(userId, toGroupId) => moveMemberMutation.mutate({ userId, toGroupId })}
        movePending={moveMemberMutation.isPending}
        active={activeTab === 'members'}
      />
      <EntriesTab
        groupId={group.id}
        isAdmin={isAdmin}
        active={activeTab === 'entries'}
        isDefault={group.is_default}
      />
      <ExtensionsTab groupId={group.id} isAdmin={isAdmin} active={activeTab === 'extensions'} onFeedback={showFeedback} />
      <SettingsTab
        group={group}
        isAdmin={isAdmin}
        active={activeTab === 'settings'}
        onFeedback={showFeedback}
      />
    </Stack>
  )
}
