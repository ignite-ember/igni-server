import { useState, useCallback, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link as RouterLink, useParams, useNavigate, useSearchParams } from 'react-router-dom'
import {
  ActionList,
  ActionMenu,
  Banner,
  Breadcrumbs,
  Button,
  ConfirmationDialog,
  Dialog,
  FormControl,
  IconButton,
  Label,
  PageHeader,
  SelectPanel,
  Stack,
  Text,
  TextInput,
  ToggleSwitch,
  UnderlineNav,
} from '@primer/react'
import type { SelectPanelItemInput } from '@primer/react'

import { Table } from '@primer/react/experimental'
import { DataTable } from '@primer/react/experimental'
import { Blankslate } from '@primer/react/experimental'
import {
  ArrowLeftIcon,
  CheckCircleIcon,
  CircleSlashIcon,
  CopyIcon,
  DiffAddedIcon,
  KebabHorizontalIcon,
  PencilIcon,
  PersonAddIcon,
  PlusIcon,
  TrashIcon,
  TriangleDownIcon,
} from '@primer/octicons-react'
import client from '../api/client'
import { KIND_BY_ID, KIND_ORDER, prettySource } from '../lib/entryKinds'
import { StatCard } from '../components/StatCard'
import { SkeletonText } from '@primer/react/experimental'
import type {
  GroupExtensionList,
  OrgGroup,
  OrgGroupDetail,
  OrgGroupList,
  OrgGroupMember,
  OrgGroupMemberList,
  OrgGroupMembershipList,
  OrgGroupEntry,
  OrgGroupEntryList,
  TeamMember,
  TeamMemberList,
  UserMe,
} from '../types'
import './cards.css'
import './GroupDetailPage.css'

type Tab = 'settings' | 'members' | 'entries' | 'extensions'

/**
 * Who is in this group, and moving people in and out of it.
 *
 * Everybody in the organisation is in exactly one group — Default until
 * an admin puts them somewhere else — so there is no "add" that is not
 * a move: bringing somebody here takes them out of their current group,
 * and taking them out of here puts them back in Default. Nobody is taken
 * out of Default; they leave it by being moved.
 */
function MembersTab({
  isAdmin,
  isDefault,
  onMoveMember,
  movePending,
  active,
}: {
  isAdmin: boolean
  /** Default keeps everybody nobody has placed elsewhere: no one is removed from it. */
  isDefault: boolean
  onMoveMember: (userId: string, toGroupId: string) => void
  movePending: boolean
  active: boolean
}) {
  const { id: groupId } = useParams<{ id: string }>()
  const queryClient = useQueryClient()

  const membersQuery = useQuery({
    queryKey: ['group-members', groupId],
    queryFn: () => client.get<OrgGroupMemberList>(`/portal/groups/${groupId}/members`).then((r) => r.data),
  })

  const teamQuery = useQuery({
    queryKey: ['team-members-select'],
    queryFn: () => client.get<TeamMemberList>('/portal/team').then((r) => r.data),
  })

  // One call for everybody's group, rather than the members of every
  // other group one request at a time.
  const membershipsQuery = useQuery({
    queryKey: ['group-memberships'],
    queryFn: () => client.get<OrgGroupMembershipList>('/portal/group-memberships').then((r) => r.data),
    enabled: isAdmin,
  })

  const groupsQuery = useQuery({
    queryKey: ['groups'],
    queryFn: () => client.get<OrgGroupList>('/portal/groups').then((r) => r.data),
    enabled: isAdmin,
  })

  const removeMutation = useMutation({
    mutationFn: (userId: string) => client.delete(`/portal/groups/${groupId}/members/${userId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['group-members'] })
      queryClient.invalidateQueries({ queryKey: ['group-memberships'] })
      queryClient.invalidateQueries({ queryKey: ['group'] })
      queryClient.invalidateQueries({ queryKey: ['groups'] })
    },
  })

  const members: OrgGroupMember[] = membersQuery.data?.members ?? []
  const teamMembers: TeamMember[] = teamQuery.data?.members ?? []
  const otherGroups = (groupsQuery.data?.groups ?? []).filter((g) => g.id !== groupId)

  const memberUserIds = new Set(members.map((m) => m.user_id))
  const assignableMembers = teamMembers.filter((m) => m.status === 'active')
  const currentGroupOf = new Map<string, string>(
    (membershipsQuery.data?.memberships ?? [])
      .filter((x) => x.group_id !== groupId)
      .map((x) => [x.user_id, x.group_name])
  )

  const [confirmRemove, setConfirmRemove] = useState<OrgGroupMember | null>(null)

  if (!active) return null

  return (
    <Stack direction="vertical" gap="normal" padding="normal">
      {membersQuery.isError && <Banner variant="critical" title="Failed to load members" />}
      {removeMutation.isError && (
        <Banner
          variant="critical"
          title={
            (removeMutation.error as { response?: { data?: { detail?: string } } }).response?.data?.detail ??
            'Failed to remove member'
          }
        />
      )}

      {/* Says what membership does, the way the configuration tab says
          what its list does. Moving somebody is not bookkeeping — it
          changes what their agent can do. */}
      <Stack direction="horizontal" justify="space-between" align="center">
        <Text size="small" className="group-detail-muted">
          Everyone here gets this group&apos;s configuration on their next sync. Everybody is in exactly one
          group, so bringing someone here moves them out of theirs.
        </Text>
        {isAdmin && (
          <>
          {membershipsQuery.isPending ? (
            <Button disabled loading>
              <PersonAddIcon /> Move someone here
            </Button>
          ) : (
            <AddMemberMenu
              members={assignableMembers}
              memberUserIds={memberUserIds}
              currentGroupOf={currentGroupOf}
              onMove={(userId) => groupId && onMoveMember(userId, groupId)}
              movePending={movePending}
            />
          )}
          </>
        )}
      </Stack>

      {members.length === 0 ? (
        <Blankslate>
          <Blankslate.Heading>No members</Blankslate.Heading>
          <Blankslate.Description>Move people from other groups into this one.</Blankslate.Description>
        </Blankslate>
      ) : (
        <Table.Container className="group-detail-table">
          <DataTable
            data={members}
            columns={[
              {
                header: 'Member',
                field: 'full_name' as const,
                rowHeader: true,
                renderCell: (row: OrgGroupMember) => (
                  <Stack direction="vertical" gap="condensed">
                    <Text weight="semibold">{row.full_name ?? row.email ?? 'Unknown'}</Text>
                    {row.full_name && <Text size="small" className="group-detail-muted">{row.email}</Text>}
                  </Stack>
                ),
              },
              {
                header: 'Added',
                field: 'added_at' as const,
                width: 120,
                renderCell: (row: OrgGroupMember) => (
                  <Text size="small" className="group-detail-muted">
                    {new Date(row.added_at).toLocaleDateString()}
                  </Text>
                ),
              },
              ...(isAdmin
                ? [
                    {
                      id: 'actions',
                      header: '',
                      field: 'id' as const,
                      width: 'auto' as const,
                      renderCell: (row: OrgGroupMember) => (
                        <Stack direction="horizontal" gap="condensed" justify="end">
                          <ActionMenu>
                            <ActionMenu.Button size="small" disabled={otherGroups.length === 0}>
                              Move to
                            </ActionMenu.Button>
                            <ActionMenu.Overlay>
                              <ActionList>
                                {otherGroups.map((g) => (
                                  <ActionList.Item key={g.id} onSelect={() => onMoveMember(row.user_id, g.id)}>
                                    {g.name}
                                  </ActionList.Item>
                                ))}
                              </ActionList>
                            </ActionMenu.Overlay>
                          </ActionMenu>
                          {!isDefault && (
                            <Button size="small" variant="danger" onClick={() => setConfirmRemove(row)}>
                              <TrashIcon /> Remove
                            </Button>
                          )}
                        </Stack>
                      ),
                    },
                  ]
                : []),
            ]}
          />
        </Table.Container>
      )}

      {confirmRemove && (
        <ConfirmationDialog
          title="Remove member"
          confirmButtonContent="Remove"
          cancelButtonContent="Cancel"
          confirmButtonType="danger"
          onClose={(gesture) => {
            const member = confirmRemove
            setConfirmRemove(null)
            if (gesture === 'confirm' && member) {
              removeMutation.mutate(member.user_id)
            }
          }}
        >
          Remove <strong>{confirmRemove.full_name ?? confirmRemove.email}</strong> from this group? They go back to
          the Default group.
        </ConfirmationDialog>
      )}
    </Stack>
  )
}

function AddMemberMenu({
  members,
  memberUserIds,
  currentGroupOf,
  onMove,
  movePending,
}: {
  members: TeamMember[]
  memberUserIds: Set<string>
  /** Everybody not in this group → the one group they are in now. */
  currentGroupOf: Map<string, string>
  onMove: (userId: string) => void
  movePending: boolean
}) {
  const [open, setOpen] = useState(false)
  const [filter, setFilter] = useState('')
  const [confirmMove, setConfirmMove] = useState<{ id: string; name: string; fromGroup: string | undefined } | null>(
    null
  )

  const filtered = members.filter((m) => {
    const f = filter.toLowerCase()
    return (
      !f ||
      (m.full_name?.toLowerCase().includes(f) ?? false) ||
      (m.email?.toLowerCase().includes(f) ?? false)
    )
  })

  function handleSelect(userId: string) {
    const user = members.find((m) => m.id === userId)
    if (!user) return
    if (memberUserIds.has(userId)) return
    setConfirmMove({ id: userId, name: user.full_name ?? user.email ?? userId, fromGroup: currentGroupOf.get(userId) })
  }

  function doMove(userId: string) {
    onMove(userId)
    setOpen(false)
    setFilter('')
  }

  return (
    <>
      <SelectPanel
        // The anchor says what it does — its own children replace the
        // placeholder SelectPanel hands it: nothing stays selected after a move.
        renderAnchor={(anchorProps) => (
          <Button {...anchorProps} trailingAction={TriangleDownIcon}>
            <PersonAddIcon /> Move someone here
          </Button>
        )}
        title="Move someone here"
        subtitle="Everybody is in exactly one group. Moving someone here takes them out of the group they are in now."
        placeholder="Select a member"
        open={open}
        onOpenChange={(o) => { setOpen(o); if (!o) setFilter('') }}
        items={filtered.map((m) => ({
          text: m.full_name ?? m.email ?? m.id,
          description: [
            m.full_name ? m.email : null,
            memberUserIds.has(m.id)
              ? 'Already here'
              : currentGroupOf.get(m.id)
                ? `Currently in ${currentGroupOf.get(m.id)}`
                : null,
          ].filter(Boolean).join(' · ') || undefined,
          id: m.id,
          disabled: memberUserIds.has(m.id) || !m.full_name && !m.email,
        }))}
        filterValue={filter}
        onFilterChange={setFilter}
        placeholderText="Filter by name or email"
        inputLabel="Search members"
        message={filtered.length === 0 ? { title: 'No matches', body: 'Try a different search.', variant: 'empty' } : undefined}
        selected={undefined}
        onSelectedChange={(item: SelectPanelItemInput | undefined) => {
          if (!item || item.id === undefined) return
          handleSelect(String(item.id))
        }}
      />

      {confirmMove && (
        <ConfirmationDialog
          title={`Move "${confirmMove.name}" to this group?`}
          confirmButtonContent={movePending ? 'Moving…' : 'Move member'}
          cancelButtonContent="Cancel"
          confirmButtonType="primary"
          onClose={(gesture) => {
            if (gesture === 'confirm') {
              doMove(confirmMove.id)
            }
            setConfirmMove(null)
          }}
        >
          <Text size="medium">
            {confirmMove.fromGroup ? (
              <>
                This member is in <Text weight="semibold">{confirmMove.fromGroup}</Text>. Moving them here takes them
                out of it.
              </>
            ) : (
              'Moving them here takes them out of the group they are in now.'
            )}
          </Text>
        </ConfirmationDialog>
      )}
    </>
  )
}

function EntriesTab({
  isAdmin,
  active,
  isDefault,
}: {
  isAdmin: boolean
  active: boolean
  /** The standard set: shown, never changed. */
  isDefault: boolean
}) {
  const { id: groupId } = useParams<{ id: string }>()
  const entriesQuery = useQuery({
    queryKey: ['group-entries', groupId],
    queryFn: () => client.get<OrgGroupEntryList>(`/portal/groups/${groupId}/entries`).then((r) => r.data),
  })

  const queryClient = useQueryClient()
  const [confirmDelete, setConfirmDelete] = useState<OrgGroupEntry | null>(null)

  const deleteMutation = useMutation({
    mutationFn: (entryId: string) => client.delete(`/portal/groups/${groupId}/entries/${entryId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['group-entries', groupId] }),
  })

  const toggleMutation = useMutation({
    mutationFn: ({ entryId, enabled }: { entryId: string; enabled: boolean }) =>
      client.patch(`/portal/groups/${groupId}/entries/${entryId}`, { enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['group-entries', groupId] }),
  })

  const entries: OrgGroupEntry[] = entriesQuery.data?.entries ?? []
  const canEdit = isAdmin && !isDefault

  if (!active) return null

  return (
    <Stack direction="vertical" gap="normal" padding="normal">
      {entriesQuery.isError && <Banner variant="critical" title="Failed to load entries" />}

      {isDefault && (
        <Banner
          variant="info"
          title="This is what igni ships"
          description="Read-only, and the group every other one starts from. Create a group from it to make your own version."
        />
      )}

      {entries.length === 0 ? (
        <Blankslate spacious>
          <Blankslate.Heading>Nothing configured yet</Blankslate.Heading>
          <Blankslate.Description>
            This group gives its members nothing yet. Add agents, MCP servers, plugins or settings —
            what is here is exactly what they get.
          </Blankslate.Description>
          {canEdit && (
            <Blankslate.PrimaryAction href={`/groups/${groupId}/entries/new`}>
              <PlusIcon /> Create first entry
            </Blankslate.PrimaryAction>
          )}
        </Blankslate>
      ) : (
        /* One table per kind, headed by it.
         *
         * A single table with a Kind column made you read every row to
         * answer "which of these are MCP servers?", and on a group that
         * is all agents it printed "Agents" nineteen times — a column
         * carrying no information. A heading answers the question
         * without reading a row, and it lets each kind show the columns
         * that mean something for it: a model only applies to an agent,
         * a source only to a plugin.
         */
        <Stack direction="vertical" gap="normal">
          <Stack direction="horizontal" justify="space-between" align="center">
            <Text size="small" className="group-detail-muted">
              Exactly what this group&apos;s members get — nothing else is shipped to them.
            </Text>
            {canEdit && (
              <Button as={RouterLink} to={`/groups/${groupId}/entries/new`} variant="primary" size="small">
                <PlusIcon /> New entry
              </Button>
            )}
          </Stack>

          {KIND_ORDER.map((kind) => {
            const rows = entries.filter((e) => e.kind === kind)
            if (rows.length === 0) return null
            const KindIcon = KIND_BY_ID[kind]?.icon ?? DiffAddedIcon
            return (
              <Table.Container key={kind} className="group-entries-table">
                <Table.Title id={`group-entries-${kind}`}>
                  {KIND_BY_ID[kind]?.label ?? kind} <Label variant="secondary">{rows.length}</Label>
                </Table.Title>
                <DataTable
                  aria-labelledby={`group-entries-${kind}`}
                  data={rows}
                  columns={[
                    {
                      header: 'Name',
                      field: 'entry_name' as const,
                      rowHeader: true,
                      renderCell: (row: OrgGroupEntry) => (
                        <Stack direction="horizontal" gap="condensed" align="center">
                          <KindIcon className="group-detail-muted" />
                          <RouterLink to={`/groups/${groupId}/entries/${row.id}/edit`}>
                            {row.entry_name}
                          </RouterLink>
                          {!row.enabled && <Label variant="secondary">Disabled</Label>}
                        </Stack>
                      ),
                    },
                    // A model is an agent's, and a source is a plugin's.
                    // Showing both columns everywhere would be four
                    // tables of em dashes.
                    ...(kind === 'agents'
                      ? [
                          {
                            header: 'Model',
                            id: 'model',
                            field: 'id' as const,
                            width: 200,
                            renderCell: (row: OrgGroupEntry) =>
                              row.model ? (
                                <Label variant="accent">{row.model}</Label>
                              ) : (
                                <Text size="small" className="group-detail-muted">
                                  Group default
                                </Text>
                              ),
                          },
                        ]
                      : []),
                    ...(kind === 'plugins'
                      ? [
                          {
                            header: 'Source',
                            id: 'source',
                            field: 'id' as const,
                            width: 200,
                            renderCell: (row: OrgGroupEntry) => (
                              <Text size="small" className="group-detail-muted">
                                {row.source_url ? prettySource(row.source_url) : 'Inline'}
                              </Text>
                            ),
                          },
                        ]
                      : []),
                    {
                      header: 'Updated',
                      field: 'updated_at' as const,
                      width: 120,
                      renderCell: (row: OrgGroupEntry) => (
                        <Text size="small" className="group-detail-muted">
                          {new Date(row.updated_at).toLocaleDateString()}
                        </Text>
                      ),
                    },
                    ...(canEdit
                      ? [
                          {
                            id: 'actions',
                            header: '',
                            field: 'id' as const,
                            width: 'auto' as const,
                            renderCell: (row: OrgGroupEntry) => (
                              <ActionMenu>
                                <ActionMenu.Anchor>
                                  <IconButton
                                    icon={KebabHorizontalIcon}
                                    aria-label={`Actions for ${row.entry_name}`}
                                    variant="invisible"
                                    size="small"
                                  />
                                </ActionMenu.Anchor>
                                <ActionMenu.Overlay>
                                  <ActionList>
                                    <ActionList.LinkItem
                                      href={`/groups/${groupId}/entries/${row.id}/edit`}
                                    >
                                      <ActionList.LeadingVisual>
                                        <PencilIcon />
                                      </ActionList.LeadingVisual>
                                      Edit
                                    </ActionList.LinkItem>
                                    <ActionList.Item
                                      onSelect={() =>
                                        toggleMutation.mutate({
                                          entryId: row.id,
                                          enabled: !row.enabled,
                                        })
                                      }
                                    >
                                      <ActionList.LeadingVisual>
                                        {row.enabled ? <CircleSlashIcon /> : <CheckCircleIcon />}
                                      </ActionList.LeadingVisual>
                                      {row.enabled ? 'Disable' : 'Enable'}
                                    </ActionList.Item>
                                    <ActionList.Divider />
                                    <ActionList.Item
                                      variant="danger"
                                      onSelect={() => setConfirmDelete(row)}
                                    >
                                      <ActionList.LeadingVisual>
                                        <TrashIcon />
                                      </ActionList.LeadingVisual>
                                      Delete
                                    </ActionList.Item>
                                  </ActionList>
                                </ActionMenu.Overlay>
                              </ActionMenu>
                            ),
                          },
                        ]
                      : []),
                  ]}
                />
              </Table.Container>
            )
          })}
        </Stack>
      )}

      {/* Deleting an entry takes something away from everyone in the
          group on their next sync, which is worth a question first. */}
      {confirmDelete && (
        <ConfirmationDialog
          title="Delete entry"
          confirmButtonContent="Delete"
          cancelButtonContent="Cancel"
          confirmButtonType="danger"
          onClose={(gesture) => {
            if (gesture === 'confirm') deleteMutation.mutate(confirmDelete.id)
            setConfirmDelete(null)
          }}
        >
          <Text>
            Delete <Text weight="semibold">{confirmDelete.entry_name}</Text>? Everyone in this group
            loses it on their next sync.
          </Text>
        </ConfirmationDialog>
      )}

    </Stack>
  )
}

/**
 * Which model this group's people get.
 *
 * Usually a LoRA adapter: a legal team pinned to a legal-review adapter,
 * engineering to another, both served by the same GPUs. The list comes
 * from the deployment, because adapter names are whatever somebody
 * passed to `--lora-modules` and typing them from memory is how a group
 * ends up pointed at a model nothing serves.
 *
 * Only what a caller does *not* specify is affected. An explicit request
 * still wins, so this cannot silently entry somebody who asked for
 * something.
 */
function GroupModel({
  groupId,
  current,
  isAdmin,
  onFeedback,
}: {
  groupId: string
  current: string | null
  isAdmin: boolean
  onFeedback: (variant: 'success' | 'critical', message: string) => void
}) {
  const queryClient = useQueryClient()

  // The portal endpoint, not /chat/models. That one authenticates a real
  // user JWT; the portal session is a different scheme, so it answers
  // 401 — and the client treats any 401 as an expired session and logs
  // the admin out. Opening this page used to do exactly that.
  const modelsQuery = useQuery({
    queryKey: ['portal-inference-models'],
    queryFn: () => client.get<{ models: string[] }>('/portal/inference/models').then((r) => r.data),
    enabled: isAdmin,
  })

  const setModel = useMutation({
    mutationFn: (model: string | null) =>
      // An empty string clears it: null already means "not supplied" on
      // a partial update.
      client.patch(`/portal/groups/${groupId}`, { default_model: model ?? '' }).then((r) => r.data),
    onSuccess: (_data, model) => {
      queryClient.invalidateQueries({ queryKey: ['group', groupId] })
      onFeedback('success', model ? `Group routed to ${model}` : 'Group uses the deployment default')
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
      onFeedback('critical', detail ?? 'Failed to set the model')
    },
  })

  const models = modelsQuery.data?.models ?? []

  // No label of its own: this renders inside a card already headed
  // "Model", and saying it twice is how a settings page starts looking
  // like a form nobody laid out.
  if (!isAdmin) {
    return (
      <Text size="medium">{current ?? 'Deployment default'}</Text>
    )
  }

  return (
    <Stack direction="horizontal" gap="condensed" align="center">
      <ActionMenu>
        <ActionMenu.Button size="small" loading={setModel.isPending}>
          {current ?? 'Deployment default'}
        </ActionMenu.Button>
        <ActionMenu.Overlay>
          <ActionList selectionVariant="single">
            <ActionList.Item selected={!current} onSelect={() => setModel.mutate(null)}>
              Deployment default
              <ActionList.Description variant="block">
                Whatever the node chosen for each request serves first.
              </ActionList.Description>
            </ActionList.Item>
            {models.length > 0 && <ActionList.Divider />}
            {models.map((model) => (
              <ActionList.Item key={model} selected={current === model} onSelect={() => setModel.mutate(model)}>
                {model}
              </ActionList.Item>
            ))}
          </ActionList>
        </ActionMenu.Overlay>
      </ActionMenu>
    </Stack>
  )
}

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
function SettingsTab({
  group,
  isAdmin,
  active,
  onFeedback,
}: {
  group: OrgGroupDetail
  isAdmin: boolean
  active: boolean
  onFeedback: (variant: 'success' | 'critical', message: string) => void
}) {
  const { id: groupId } = useParams<{ id: string }>()

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

/**
 * Which approved extensions this group's people get.
 *
 * A super admin approving a service is trust; whether a group uses its
 * extension is the org admin's, and it is off until they switch it on.
 * The Default group can have extensions too: it is read-only as the copy
 * of what igni ships, but this is not part of that copy, and most people
 * are in it.
 */
function ExtensionsTab({
  isAdmin,
  active,
  onFeedback,
}: {
  isAdmin: boolean
  active: boolean
  onFeedback: (variant: 'success' | 'critical', message: string) => void
}) {
  const { id: groupId } = useParams<{ id: string }>()
  const queryClient = useQueryClient()

  const extensionsQuery = useQuery({
    queryKey: ['group-extensions', groupId],
    queryFn: () => client.get<GroupExtensionList>(`/portal/groups/${groupId}/extensions`).then((r) => r.data),
    enabled: active && isAdmin,
  })

  const setEnabled = useMutation({
    mutationFn: ({ extensionId, enabled }: { extensionId: string; enabled: boolean }) =>
      client.put(`/portal/groups/${groupId}/extensions/${extensionId}`, { enabled }),
    onSuccess: (_data, { enabled }) => {
      queryClient.invalidateQueries({ queryKey: ['group-extensions', groupId] })
      onFeedback('success', enabled ? 'Extension switched on for this group' : 'Extension switched off for this group')
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
      onFeedback('critical', detail ?? 'Failed to change the extension')
    },
  })

  if (!active) return null

  if (!isAdmin) {
    return <Text size="medium">Only an organisation admin decides which extensions a group gets.</Text>
  }

  if (extensionsQuery.isLoading) {
    return <SkeletonText size="bodyMedium" maxWidth={300} />
  }

  const extensions = extensionsQuery.data?.extensions ?? []

  if (extensions.length === 0) {
    return (
      <Blankslate>
        <Blankslate.Heading>No extensions to give</Blankslate.Heading>
        <Blankslate.Description>
          An extension appears here once a super admin approves the service that ships it.
        </Blankslate.Description>
      </Blankslate>
    )
  }

  return (
    <Stack gap="normal">
      <Text size="medium">
        An approved extension reaches nobody until you switch it on for a group. People in this group get the ones
        switched on here.
      </Text>
      <Table.Container>
        <DataTable
          aria-label="Extensions"
          data={extensions.map((extension) => ({ id: extension.extension_id, ...extension }))}
          columns={[
            {
              header: 'Extension',
              field: 'name',
              rowHeader: true,
              renderCell: (row) => (
                <Stack gap="none">
                  <Text weight="semibold" id={`group-extension-${row.extension_id}`}>
                    {row.name}
                  </Text>
                  {row.description && <Text size="small">{row.description}</Text>}
                </Stack>
              ),
            },
            {
              header: 'Service',
              field: 'service_url',
              renderCell: (row) => (
                <a href={row.service_url} target="_blank" rel="noopener noreferrer">
                  {row.service_url}
                </a>
              ),
            },
            {
              header: 'This group',
              field: 'enabled',
              renderCell: (row) => (
                <ToggleSwitch
                  size="small"
                  aria-labelledby={`group-extension-${row.extension_id}`}
                  checked={row.enabled}
                  loading={setEnabled.isPending && setEnabled.variables?.extensionId === row.extension_id}
                  onClick={() => setEnabled.mutate({ extensionId: row.extension_id, enabled: !row.enabled })}
                />
              ),
            },
          ]}
        />
      </Table.Container>
    </Stack>
  )
}

/**
 * Copy a group: its entries, its extensions and its settings, under a
 * new name — and nobody in it, because each person is in exactly one
 * group and stays where they are. The copy is an ordinary group, so
 * copying the read-only Default one is how to get an editable version.
 */
function CopyGroupDialog({
  group,
  returnFocusRef,
  onClose,
  onCopied,
}: {
  group: OrgGroupDetail
  returnFocusRef: React.RefObject<HTMLButtonElement | null>
  onClose: () => void
  onCopied: (copy: OrgGroup) => void
}) {
  const [name, setName] = useState(`${group.name} (copy)`)

  const copyMutation = useMutation({
    mutationFn: (copyName: string) =>
      client.post<OrgGroup>(`/portal/groups/${group.id}/copy`, { name: copyName }).then((r) => r.data),
    onSuccess: onCopied,
  })

  const submit = () => {
    if (!name.trim() || copyMutation.isPending) return
    copyMutation.mutate(name.trim())
  }

  return (
    <Dialog
      title="Copy group"
      onClose={onClose}
      returnFocusRef={returnFocusRef as React.RefObject<HTMLElement>}
      footerButtons={[
        { content: 'Cancel', onClick: onClose },
        {
          content: copyMutation.isPending ? 'Copying…' : 'Copy',
          buttonType: 'primary',
          onClick: submit,
          disabled: !name.trim() || copyMutation.isPending,
        },
      ]}
    >
      <Stack gap="normal">
        <Text size="small" className="group-detail-muted">
          The new group gets everything <Text weight="semibold">{group.name}</Text> gives its people — its
          configuration, extensions, model and branding. Nobody is moved into it.
        </Text>
        <FormControl>
          <FormControl.Label>Name</FormControl.Label>
          <TextInput value={name} onChange={(e) => setName(e.target.value)} block autoFocus />
        </FormControl>
        {copyMutation.isError && (
          <Text size="small" className="color-fg-danger">
            {(copyMutation.error as { response?: { data?: { detail?: string } } }).response?.data?.detail ??
              'The group was not copied.'}
          </Text>
        )}
      </Stack>
    </Dialog>
  )
}

export function GroupDetailPage() {
  const { id: groupId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const meQuery = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
  })

  const groupQuery = useQuery({
    queryKey: ['group', groupId],
    queryFn: () => client.get<OrgGroupDetail>(`/portal/groups/${groupId}`).then((r) => r.data),
  })

  const isAdmin = meQuery.data?.role === 'admin'
  const group = groupQuery.data

  const [searchParams, setSearchParams] = useSearchParams()
  const tabParam = searchParams.get('tab')
  const activeTab: Tab =
    tabParam === 'entries'
      ? 'entries'
      : tabParam === 'members'
        ? 'members'
        : tabParam === 'extensions'
          ? 'extensions'
          : 'settings'
  const setActiveTab = (tab: Tab) => {
    const next = new URLSearchParams(searchParams)
    if (tab === 'settings') {
      next.delete('tab')
    } else {
      next.set('tab', tab)
    }
    setSearchParams(next, { replace: true })
  }
  const [feedbackBanner, setFeedbackBanner] = useState<{ variant: 'success' | 'critical'; message: string } | null>(null)

  const showFeedback = useCallback((variant: 'success' | 'critical', message: string) => {
    setFeedbackBanner({ variant, message })
    setTimeout(() => setFeedbackBanner(null), 5000)
  }, [])


  const moveMemberMutation = useMutation({
    mutationFn: ({ userId, toGroupId }: { userId: string; toGroupId: string }) =>
      client.post(`/portal/groups/${toGroupId}/members`, { user_id: userId }),
    onSuccess: (_data, { toGroupId }) => {
      queryClient.invalidateQueries({ queryKey: ['group-members'] })
      queryClient.invalidateQueries({ queryKey: ['group-memberships'] })
      queryClient.invalidateQueries({ queryKey: ['group'] })
      queryClient.invalidateQueries({ queryKey: ['groups'] })
      showFeedback('success', toGroupId === groupId ? 'Member moved to this group' : 'Member moved out of this group')
    },
    onError: (err: unknown) => {
      const e = err as { response?: { data?: { detail?: string } } }
      const detail = e.response?.data?.detail
      showFeedback('critical', detail ?? 'Failed to move member')
    },
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

      {feedbackBanner && (
        <Banner
          variant={feedbackBanner.variant}
          title={feedbackBanner.message}
          onDismiss={() => setFeedbackBanner(null)}
        />
      )}


      <MembersTab
        isAdmin={isAdmin}
        isDefault={group.is_default}
        onMoveMember={(userId, toGroupId) => moveMemberMutation.mutate({ userId, toGroupId })}
        movePending={moveMemberMutation.isPending}
        active={activeTab === 'members'}
      />
      <EntriesTab
        isAdmin={isAdmin}
        active={activeTab === 'entries'}
        isDefault={group.is_default}
      />
      <ExtensionsTab isAdmin={isAdmin} active={activeTab === 'extensions'} onFeedback={showFeedback} />
      <SettingsTab
        group={group}
        isAdmin={isAdmin}
        active={activeTab === 'settings'}
        onFeedback={showFeedback}
      />
    </Stack>
  )
}

