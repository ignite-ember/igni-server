import { useCallback, useState } from 'react'
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
import { closestTool, unknownTools } from '../lib/agentTools'
import './cards.css'
import { AgentModelField } from '../components/AgentModelField'
import { ContentEditor } from '../components/ContentEditor'
import type { OrgGroupDetail, OrgGroupEntry } from '../types'
import { KIND_BY_ID, type ContentType } from '../lib/entryKinds'

/**
 * The tool names this deployment offers.
 *
 * From the server, so the live warning and the one on save check
 * against the same list rather than two copies that can disagree.
 */
function useKnownTools(kind: string): string[] {
  const toolsQuery = useQuery({
    queryKey: ['agent-tools'],
    queryFn: () => client.get<{ tools: string[] }>('/portal/agent-tools').then((r) => r.data),
    enabled: kind === 'agents',
    staleTime: 5 * 60 * 1000,
  })
  return kind === 'agents' ? (toolsQuery.data?.tools ?? []) : []
}

/** The inline note under the editor, or nothing. */
function ToolWarning({ names, known }: { names: string[]; known: string[] }) {
  if (names.length === 0) return null
  return (
    <Text size="small" className="entry-tool-warning">
      {names
        .map((name) => {
          const guess = closestTool(name, known)
          return guess ? `${name} — did you mean ${guess}?` : name
        })
        .join(' · ')}{' '}
      {names.length === 1 ? 'is not a tool igni knows' : 'are not tools igni knows'}. This agent
      will fail when something calls it.
    </Text>
  )
}

export function EditEntryPage() {
  const { id: groupId, entryId } = useParams<{ id: string; entryId: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const entryQuery = useQuery({
    queryKey: ['entry', groupId, entryId],
    queryFn: async () => {
      const list = await client
        .get<{ entries: OrgGroupEntry[] }>(`/portal/groups/${groupId}/entries`)
        .then((r) => r.data)
      return list.entries.find((o) => o.id === entryId) ?? null
    },
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

  const entry = entryQuery.data

  return (
    <EditEntryForm
      groupId={groupId!}
      groupName={groupQuery.data?.name}
      groupDefaultModel={groupQuery.data?.default_model}
      readOnly={!!groupQuery.data?.is_default}
      entry={entry}
      onSaved={() => {
        queryClient.invalidateQueries({ queryKey: ['group-entries', groupId] })
        navigate(`/groups/${groupId}?tab=entries`)
      }}
      onClose={() => navigate(`/groups/${groupId}?tab=entries`)}
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
  onSaved: () => void
  onClose: () => void
}

function EditEntryForm({
  groupId,
  groupName,
  groupDefaultModel,
  readOnly,
  entry,
  onSaved,
}: EditEntryFormProps) {
  const queryClient = useQueryClient()
  const [feedbackBanner, setFeedbackBanner] = useState<{ variant: 'success' | 'critical'; message: string } | null>(null)
  const [entryName, setEntryName] = useState(entry.entry_name)
  const [content, setContent] = useState(entry.content)
  const [enabled, setEnabled] = useState(entry.enabled)
  const [model, setModel] = useState<string | null>(entry.model)
  const [nameError, setNameError] = useState<string | null>(null)
  const [contentError, setContentError] = useState<string | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])

  const kind = entry.kind
  const spec = KIND_BY_ID[kind]
  const knownTools = useKnownTools(kind)
  const badTools = unknownTools(content, knownTools)
  const contentType: ContentType = (entry.content_type as ContentType) ?? spec?.contentType ?? 'markdown'

  const updateMutation = useMutation({
    mutationFn: () =>
      client.patch(`/portal/groups/${groupId}/entries/${entry.id}`, {
        content,
        enabled,
        // An empty string clears it: null already means "not supplied"
        // on a partial update.
        ...(kind === 'agents' ? { model: model ?? '' } : {}),
      }).then((r) => r.data),
    onSuccess: (data: { warnings?: string[] }) => {
      // See NewEntryPage: a warning nobody reads is not a warning.
      if (data?.warnings?.length) {
        setWarnings(data.warnings)
        return
      }
      showFeedback('success', 'Entry updated')
      onSaved()
    },
    onError: (err: unknown) => {
      const e = err as { response?: { data?: { detail?: string } } }
      const detail = e.response?.data?.detail
      setContentError(detail ?? 'Failed to update entry')
    },
  })

  const showFeedback = useCallback((variant: 'success' | 'critical', message: string) => {
    setFeedbackBanner({ variant, message })
    setTimeout(() => setFeedbackBanner(null), 5000)
  }, [])

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault()
    const trimmed = entryName.trim()
    if (!trimmed) {
      setNameError('Entry name is required')
      return
    }
    if (!content.trim()) {
      setContentError('Content is required')
      return
    }
    setNameError(null)
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
        <Breadcrumbs.Item href={`/groups/${groupId}?tab=entries`}>
          Entries
        </Breadcrumbs.Item>
        <Breadcrumbs.Item selected>{entry.entry_name}</Breadcrumbs.Item>
      </Breadcrumbs>

      <PageHeader>
        <PageHeader.TitleArea>
          <Button
            variant="invisible"
            as={RouterLink}
            to={`/groups/${groupId}?tab=entries`}
            aria-label="Back to entries"
          >
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

      {feedbackBanner && (
        <Banner
          variant={feedbackBanner.variant}
          title={feedbackBanner.message}
          onDismiss={() => setFeedbackBanner(null)}
        />
      )}

      <form onSubmit={handleSubmit}>
        <Stack direction="vertical" gap="normal" style={{ maxWidth: 800 }}>
          <FormControl>
            <FormControl.Label>Entry name</FormControl.Label>
            <TextInput
              value={entryName}
              onChange={(e) => { setEntryName(e.currentTarget.value); setNameError(null) }}
              placeholder="e.g. code-reviewer, github-mcp"
              validationStatus={nameError ? 'error' : undefined}
              readOnly={readOnly}
              autoFocus={!readOnly}
              block
            />
            {nameError && <FormControl.Validation variant="error">{nameError}</FormControl.Validation>}
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
            <Checkbox
              checked={enabled}
              onChange={(e) => setEnabled(e.currentTarget.checked)}
            />
            <FormControl.Label>Enabled</FormControl.Label>
            <FormControl.Caption>When disabled, this entry is skipped at runtime.</FormControl.Caption>
          </FormControl>

          <Stack direction="horizontal" gap="condensed">
            {readOnly ? (
              <Button as={RouterLink} to={`/groups/${groupId}?tab=entries`}>
                Back
              </Button>
            ) : (
              <>
                <Button type="submit" variant="primary" disabled={updateMutation.isPending}>
                  {updateMutation.isPending ? 'Saving…' : 'Save entry'}
                </Button>
                <Button as={RouterLink} to={`/groups/${groupId}?tab=entries`} disabled={updateMutation.isPending}>
                  Cancel
                </Button>
                <Button
                  type="button"
                  variant="danger"
                  disabled={updateMutation.isPending}
                  onClick={async () => {
                    if (!window.confirm(`Delete entry "${entry.entry_name}"?`)) return
                    try {
                      await client.delete(`/portal/groups/${groupId}/entries/${entry.id}`)
                      queryClient.invalidateQueries({ queryKey: ['group-entries', groupId] })
                      showFeedback('success', 'Entry deleted')
                      onSaved()
                    } catch {
                      showFeedback('critical', 'Failed to delete entry')
                    }
                  }}
                >
                  Delete
                </Button>
              </>
            )}
          </Stack>
        </Stack>
      </form>
    </Stack>
  )
}
