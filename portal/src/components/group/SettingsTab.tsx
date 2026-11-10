import { useQuery } from '@tanstack/react-query'
import { Link as RouterLink } from 'react-router-dom'
import { Banner, Button, Stack, Text } from '@primer/react'
import { PencilIcon } from '@primer/octicons-react'

import client from '../../api/client'
import { KIND_BY_ID, KIND_ORDER } from '../../lib/entryKinds'
import type { OrgGroupDetail, OrgGroupEntryList } from '../../types'
import { StatCard } from '../StatCard'
import { GroupModel } from './GroupModel'
import type { ShowFeedback } from './feedback'

/**
 * Everything about the group itself, in one place.
 *
 * These two settings used to live apart — the model in the page header,
 * the replacement menu inside the entries tab — which meant a group's
 * configuration was split across a header and a tab body, and half of
 * it was invisible unless you happened to be on the right tab. They are
 * the same kind of thing: what this group does to its people's setup.
 *
 * Cards rather than a form: each one is independent and saves on
 * change, so there is nothing to submit and no state to lose.
 */
export function SettingsTab({
  group,
  isAdmin,
  active,
  onFeedback,
}: {
  group: OrgGroupDetail
  isAdmin: boolean
  active: boolean
  onFeedback: ShowFeedback
}) {
  const groupId = group.id

  // The counts behind the tab labels, broken down. Answers "what does
  // this group actually give people" without opening Configuration and
  // counting rows.
  const entriesQuery = useQuery({
    queryKey: ['group-entries', groupId],
    queryFn: () => client.get<OrgGroupEntryList>(`/portal/groups/${groupId}/entries`).then((r) => r.data),
    enabled: active,
  })
  const entries = entriesQuery.data?.entries ?? []

  if (!active) return null

  // The standard set is read-only: it is what igni ships and the group
  // every other one starts from, so an org that edited it would have no
  // reference point left and nothing to copy.
  const canEdit = isAdmin && !group.is_default

  return (
    <Stack direction="vertical" gap="normal" padding="normal">
      {group.is_default && (
        <Banner
          variant="info"
          title="This is what igni ships"
          description="Read-only. Create a group from it to make your own version."
        />
      )}

      <Stack direction="horizontal" gap="normal" wrap="wrap">
        <StatCard
          label="Members"
          value={String(group.member_count)}
          sub={group.member_count === 1 ? 'person' : 'people'}
        />
        {KIND_ORDER.map((kind) => {
          const count = entries.filter((e) => e.kind === kind).length
          if (count === 0) return null
          const spec = KIND_BY_ID[kind]
          return (
            <StatCard
              key={kind}
              label={spec?.label ?? kind}
              value={String(count)}
              sub={count === 1 ? spec?.singular : `${spec?.singular}s`}
            />
          )
        })}
      </Stack>

      <Stack className="settings-card" padding="normal" gap="condensed">
        <Stack direction="horizontal" justify="space-between" align="center">
          <Text className="settings-section-heading">Description</Text>
          {canEdit && (
            <Button as={RouterLink} to={`/groups/${groupId}/edit`} size="small">
              <PencilIcon /> Edit
            </Button>
          )}
        </Stack>
        <Text size="small" className="settings-section-caption">
          What this group is for. Members do not see it; it is here so the next admin knows.
        </Text>
        <Text size="medium" className={group.description ? undefined : 'group-detail-muted'}>
          {group.description || 'No description'}
        </Text>
      </Stack>

      <Stack className="settings-card" padding="normal" gap="condensed">
        <Text className="settings-section-heading">Model</Text>
        <Text size="small" className="settings-section-caption">
          Which model — usually a LoRA adapter — this group&apos;s people reach when they name none
          themselves. An explicit request still wins, and an individual agent can name its own.
        </Text>
        <GroupModel
          groupId={group.id}
          current={group.default_model}
          isAdmin={canEdit}
          onFeedback={onFeedback}
        />
      </Stack>

    </Stack>
  )
}
