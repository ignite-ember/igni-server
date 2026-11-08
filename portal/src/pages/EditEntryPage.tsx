import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Banner,
  Breadcrumbs,
  Button,
  Checkbox,
  FormControl,
  PageHeader,
  Stack,
  Text,
  TextInput,
} from '@primer/react'
import { ArrowLeftIcon } from '@primer/octicons-react'
import { useNavigate, useParams, Link as RouterLink } from 'react-router-dom'
import client from '../api/client'
import { refusalOf } from '../api/refusal'
import { unknownTools } from '../lib/agentTools'
import { useKnownTools } from '../hooks/useKnownTools'
import './cards.css'
import { AgentModelField } from '../components/AgentModelField'
import { ContentEditor } from '../components/ContentEditor'
import { DeleteEntry } from '../components/entry/DeleteEntry'
import { ToolWarning } from '../components/entry/ToolWarning'
import type { OrgGroupDetail, OrgGroupEntry, OrgGroupEntryList } from '../types'
import { KIND_BY_ID, type ContentType } from '../lib/entryKinds'

export function EditEntryPage() {
  const { id: groupId, entryId } = useParams<{ id: string; entryId: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  // The API answers entries a group at a time; sharing the group page's
  // query means opening an entry from there reads the list it already has.
  const entryQuery = useQuery({
    queryKey: ['group-entries', groupId],
    queryFn: () => client.get<OrgGroupEntryList>(`/portal/groups/${groupId}/entries`).then((r) => r.data),
    select: (list) => list.entries.find((entry) => entry.id === entryId) ?? null,
    enabled: !!groupId && !!entryId,
  })

  const groupQuery = useQuery({
    queryKey: ['group', groupId],
    queryFn: () => client.get<OrgGroupDetail>(`/portal/groups/${groupId}`).then((r) => r.data),
    enabled: !!groupId,
  })

  if (entryQuery.isLoading || groupQuery.isLoading) {
    return (
      <Stack gap="spacious" padding="normal">
        <Text size="small" className="group-detail-muted">Loading entry…</Text>
      </Stack>
    )
  }

  if (entryQuery.isError || !entryQuery.data) {
    return (
      <Stack gap="spacious">
        <Banner
          variant="critical"
          title="Entry not found"
          description="This entry may have been deleted or never existed."
        />
        <Button as={RouterLink} to={`/groups/${groupId}?tab=entries`}>
          <ArrowLeftIcon /> Back to entries
        </Button>
      </Stack>
    )
  }

  return (
    <EditEntryForm
      groupId={groupId!}
      groupName={groupQuery.data?.name}
      groupDefaultModel={groupQuery.data?.default_model}
      readOnly={!!groupQuery.data?.is_default}
      entry={entryQuery.data}
      onDone={() => {
        queryClient.invalidateQueries({ queryKey: ['group-entries', groupId] })
        navigate(`/groups/${groupId}?tab=entries`)
      }}
    />
  )
}

interface EditEntryFormProps {
  groupId: string
  groupName: string | undefined
  groupDefaultModel: string | null | undefined
  /**
   * The group holds what igni ships. Reading an entry is the point —
   * you look at the shipped reviewer before copying the group — but
   * nothing here can be saved, and offering a Save button that always
   * fails is worse than not offering one.
   */
  readOnly: boolean
  entry: OrgGroupEntry
  /** Saved without warnings, or deleted: back to the group's entries. */
  onDone: () => void
}

function EditEntryForm({ groupId, groupName, groupDefaultModel, readOnly, entry, onDone }: EditEntryFormProps) {
  const [content, setContent] = useState(entry.content)
  const [enabled, setEnabled] = useState(entry.enabled)
  const [model, setModel] = useState<string | null>(entry.model)
  const [contentError, setContentError] = useState<string | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])
  const [deleteRefusal, setDeleteRefusal] = useState<string | null>(null)

  const kind = entry.kind
  const spec = KIND_BY_ID[kind]
  const knownTools = useKnownTools(kind)
  const badTools = unknownTools(content, knownTools)
  const contentType: ContentType = entry.content_type ?? spec?.contentType ?? 'markdown'
  const entriesPath = `/groups/${groupId}?tab=entries`

  const updateMutation = useMutation({
    mutationFn: () =>
      client
        .patch<OrgGroupEntryList>(`/portal/groups/${groupId}/entries/${entry.id}`, {
          content,
          enabled,
          // An empty string clears it: null already means "not supplied"
          // on a partial update.
          ...(kind === 'agents' ? { model: model ?? '' } : {}),
        })
        .then((r) => r.data),
    onSuccess: (data) => {
      // See NewEntryPage: a warning nobody reads is not a warning.
      if (data.warnings?.length) {
        setWarnings(data.warnings)
        return
      }
      onDone()
    },
    onError: (err) => setContentError(refusalOf(err, 'Failed to update entry')),
  })

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault()
    if (!content.trim()) {
      setContentError('Content is required')
      return
    }
    setContentError(null)
    updateMutation.mutate()
  }

  return (
    <Stack gap="spacious">
      <Breadcrumbs>
        <Breadcrumbs.Item href="/groups">Groups</Breadcrumbs.Item>
        {groupName ? (
          <Breadcrumbs.Item href={`/groups/${groupId}`}>{groupName}</Breadcrumbs.Item>
        ) : null}
        <Breadcrumbs.Item href={entriesPath}>Entries</Breadcrumbs.Item>
        <Breadcrumbs.Item selected>{entry.entry_name}</Breadcrumbs.Item>
      </Breadcrumbs>

      <PageHeader>
        <PageHeader.TitleArea>
          <Button variant="invisible" as={RouterLink} to={entriesPath} aria-label="Back to entries">
            <ArrowLeftIcon />
          </Button>
          <PageHeader.Title as="h1">{entry.entry_name}</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text size="medium">
            {readOnly ? 'Viewing' : 'Editing'} <Text weight="semibold">{entry.kind}</Text> entry. Content type:{' '}
            <Text weight="semibold">{contentType}</Text>
          </Text>
        </PageHeader.Description>
      </PageHeader>

      {readOnly && (
        <Banner
          variant="info"
          title="This is what igni ships"
          description="Read-only. Create a group from this one to make a version you can edit."
        />
      )}

      {warnings.map((warning) => (
        <Banner
          key={warning}
          variant="warning"
          title="Saved, but this will not run"
          description={warning}
          onDismiss={() => setWarnings((rest) => rest.filter((w) => w !== warning))}
        />
      ))}

      {deleteRefusal && (
        <Banner
          variant="critical"
          title="Entry not deleted"
          description={deleteRefusal}
          onDismiss={() => setDeleteRefusal(null)}
        />
      )}

      <form onSubmit={handleSubmit}>
        <Stack direction="vertical" gap="normal" style={{ maxWidth: 800 }}>
          {/* The API has no rename: an entry is known by its name to every
              synced machine. Shown so you know which one this is. */}
          <FormControl>
            <FormControl.Label>Entry name</FormControl.Label>
            <TextInput value={entry.entry_name} readOnly block />
            {!readOnly && <FormControl.Caption>An entry's name cannot be changed.</FormControl.Caption>}
          </FormControl>

          {kind === 'agents' && (
            <AgentModelField
              value={model}
              onChange={setModel}
              groupDefault={groupDefaultModel}
              disabled={readOnly}
            />
          )}

          {badTools.length > 0 && <ToolWarning names={badTools} known={knownTools} />}

          <ContentEditor
            value={content}
            onChange={(next) => {
              setContent(next)
              setContentError(null)
            }}
            contentType={contentType}
            readOnly={readOnly}
            error={contentError}
          />

          <FormControl disabled={readOnly}>
            <Checkbox checked={enabled} onChange={(e) => setEnabled(e.currentTarget.checked)} />
            <FormControl.Label>Enabled</FormControl.Label>
            <FormControl.Caption>When disabled, this entry is skipped at runtime.</FormControl.Caption>
          </FormControl>

          <Stack direction="horizontal" gap="condensed">
            {readOnly ? (
              <Button as={RouterLink} to={entriesPath}>
                Back
              </Button>
            ) : (
              <>
                <Button type="submit" variant="primary" disabled={updateMutation.isPending}>
                  {updateMutation.isPending ? 'Saving…' : 'Save entry'}
                </Button>
                <Button as={RouterLink} to={entriesPath} disabled={updateMutation.isPending}>
                  Cancel
                </Button>
                <DeleteEntry
                  groupId={groupId}
                  entry={entry}
                  disabled={updateMutation.isPending}
                  onDeleted={onDone}
                  onRefused={setDeleteRefusal}
                />
              </>
            )}
          </Stack>
        </Stack>
      </form>
    </Stack>
  )
}
