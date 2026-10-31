import { useState } from 'react'
import { keepPreviousData, useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link as RouterLink, useNavigate } from 'react-router-dom'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import {
  ActionMenu,
  ActionList,
  Banner,
  Button,
  ConfirmationDialog,
  IconButton,
  Label,
  PageHeader,
  Pagination,
  RelativeTime,
  Stack,
  Text,
} from '@primer/react'
import { Blankslate } from '@primer/react/experimental'
import {
  KebabHorizontalIcon,
  PeopleIcon,
  PlusIcon,
  TrashIcon,
  ArrowRightIcon,
  GitBranchIcon,
} from '@primer/octicons-react'
import client from '../api/client'
import type { OrgGroup, OrgGroupList, UserMe } from '../types'
import './GroupsPage.css'
import { countOf } from '../utils/plural'

const PAGE_SIZE = 10

interface GroupRow extends OrgGroup {
  member_count?: number
  entry_count?: number
}

function GroupRow({
  group,
  isAdmin,
  onDelete,
  onNavigate,
}: {
  group: GroupRow
  isAdmin: boolean
  onDelete: (group: OrgGroup) => void
  onNavigate: (id: string) => void
}) {
  return (
    <div className="groups-row groups-grid-row">
      <div className="groups-col-name">
        <RouterLink to={`/groups/${group.id}`} className="groups-row-link">
          <GitBranchIcon size={14} />
          <Text weight="semibold" size="small">{group.name}</Text>
        </RouterLink>
        {group.description && (
          <Text size="small" className="groups-muted groups-row-desc">{group.description}</Text>
        )}
        {(group.default_model || group.is_default) && (
          <Stack direction="horizontal" gap="condensed" align="center" className="groups-row-tags">
            {group.is_default && <Label variant="secondary">What igni ships</Label>}
            {group.default_model && <Label variant="accent">{group.default_model}</Label>}
          </Stack>
        )}
      </div>
      <div className="groups-col-members">
        <PeopleIcon size={14} />
        <Text size="small">{group.member_count ?? 0}</Text>
      </div>
      <div className="groups-col-entries">
        <Text size="small">{group.entry_count ?? 0}</Text>
      </div>
      <div className="groups-col-created">
        <Text size="small" className="groups-muted">
          <RelativeTime datetime={group.created_at} />
        </Text>
      </div>
      {isAdmin && (
        <div className="groups-col-actions">
          <ActionMenu>
            <ActionMenu.Anchor>
              <IconButton
                icon={KebabHorizontalIcon}
                variant="invisible"
                size="small"
                aria-label={`Actions for ${group.name}`}
              />
            </ActionMenu.Anchor>
            <ActionMenu.Overlay>
              <ActionList>
                <ActionList.Item onClick={() => onNavigate(group.id)}>
                  <ActionList.LeadingVisual><ArrowRightIcon /></ActionList.LeadingVisual>
                  View details
                </ActionList.Item>
                {!group.is_default && (
                  <ActionList.Item variant="danger" onSelect={() => onDelete(group)}>
                    <ActionList.LeadingVisual><TrashIcon /></ActionList.LeadingVisual>
                    Delete
                  </ActionList.Item>
                )}
              </ActionList>
            </ActionMenu.Overlay>
          </ActionMenu>
        </div>
      )}
    </div>
  )
}

export function GroupsPage() {
  useDocumentTitle('Groups')
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(0)

  const meQuery = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
  })

  const groupsQuery = useQuery({
    queryKey: ['groups', page],
    queryFn: () =>
      client.get<OrgGroupList>('/portal/groups', {
        params: { offset: page * PAGE_SIZE, limit: PAGE_SIZE },
      }).then((r) => r.data),
    placeholderData: keepPreviousData,
  })

  const isAdmin = meQuery.data?.role === 'admin'
  const data = groupsQuery.data
  const rows: GroupRow[] = data?.groups ?? []
  const total = data?.total ?? 0
  const totalPages = Math.ceil(total / PAGE_SIZE)

  const [confirmDelete, setConfirmDelete] = useState<OrgGroup | null>(null)

  const deleteMutation = useMutation({
    mutationFn: (groupId: string) => client.delete(`/portal/groups/${groupId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['groups'] })
      setConfirmDelete(null)
    },
    onError: (err: unknown) => {
      console.error('Failed to delete group', err)
    },
  })

  if (meQuery.isLoading) {
    return (
      <Stack gap="spacious">
        <PageHeader>
          <PageHeader.TitleArea>
            <PageHeader.Title as="h1">Groups</PageHeader.Title>
          </PageHeader.TitleArea>
        </PageHeader>
      </Stack>
    )
  }

  return (
    <Stack gap="spacious">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.Title as="h1">Groups</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Actions>
          {isAdmin && (
            <Button as={RouterLink} to="/groups/new">
              <PlusIcon /> New group
            </Button>
          )}
        </PageHeader.Actions>
        <PageHeader.Description>
          <Text size="medium">{countOf(total, 'group')}</Text>
        </PageHeader.Description>
      </PageHeader>

      {groupsQuery.isError && (
        <Banner variant="critical" title="Failed to load groups" description="Please try refreshing the page." />
      )}

      {rows.length === 0 ? (
        <Blankslate>
          <Blankslate.Heading>No groups yet</Blankslate.Heading>
          <Blankslate.Description>
            Create groups to assign members and configure entry policies for your organization.
          </Blankslate.Description>
          {isAdmin && (
            <Blankslate.PrimaryAction href="/groups/new">
              <PlusIcon /> Create first group
            </Blankslate.PrimaryAction>
          )}
        </Blankslate>
      ) : (
        <>
          <div className="groups-table">
            <div className="groups-header">
              <div className="groups-grid-row">
                <div className="groups-col-name">
                  <Text size="small" weight="semibold">Name</Text>
                </div>
                <div className="groups-col-members">
                  <Text size="small" weight="semibold">Members</Text>
                </div>
                <div className="groups-col-entries">
                  <Text size="small" weight="semibold">Entries</Text>
                </div>
                <div className="groups-col-created">
                  <Text size="small" weight="semibold">Created</Text>
                </div>
                {isAdmin && <div className="groups-col-actions" />}
              </div>
            </div>
            {rows.map((group) => (
              <GroupRow
                key={group.id}
                group={group}
                isAdmin={isAdmin}
                onDelete={(g) => setConfirmDelete(g)}
                onNavigate={(id) => navigate(`/groups/${id}`)}
              />
            ))}
          </div>

          {totalPages > 1 && (
            <Stack align="end" style={{ marginTop: -24 }}>
              <nav className="pagination-sm">
                <Pagination
                  pageCount={totalPages}
                  currentPage={page + 1}
                  onPageChange={(_, newPage) => { setPage(newPage - 1) }}
                />
              </nav>
            </Stack>
          )}
        </>
      )}

      {confirmDelete && (
        <ConfirmationDialog
          title="Delete group"
          confirmButtonContent="Delete"
          cancelButtonContent="Cancel"
          confirmButtonType="danger"
          onClose={(gesture) => {
            const group = confirmDelete
            setConfirmDelete(null)
            if (gesture === 'confirm' && group) {
              deleteMutation.mutate(group.id)
            }
          }}
        >
          <Text size="medium">
            Are you sure you want to delete <Text weight="semibold">{confirmDelete.name}</Text>? Its entries go with it and its people go back to the Default group. This cannot be undone.
          </Text>
        </ConfirmationDialog>
      )}
    </Stack>
  )
}
