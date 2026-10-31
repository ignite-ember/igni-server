import { useState, useCallback } from 'react'
import { useParams, useNavigate, Link as RouterLink } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Banner,
  Breadcrumbs,
  Button,
  FormControl,
  PageHeader,
  Select,
  Stack,
  Text,
  TextInput,
} from '@primer/react'
import { ArrowLeftIcon } from '@primer/octicons-react'
import client from '../api/client'
import { closestTool, unknownTools } from '../lib/agentTools'
import './cards.css'
import { AgentModelField } from '../components/AgentModelField'
import type { OrgGroupDetail } from '../types'
import { ENTRY_KINDS, KIND_BY_ID, type ContentType } from '../lib/entryKinds'
import { ContentEditor } from '../components/ContentEditor'

type EntryKind = string


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

export function NewEntryPage() {
  const { id: groupId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  // Only for the model field, which shows what an agent inherits when it
  // names none of its own.
  const groupQuery = useQuery({
    queryKey: ['group', groupId],
    queryFn: () => client.get<OrgGroupDetail>(`/portal/groups/${groupId}`).then((r) => r.data),
    enabled: !!groupId,
  })

  const [feedbackBanner, setFeedbackBanner] = useState<{ variant: 'success' | 'critical'; message: string } | null>(null)

  const [kind, setKind] = useState<EntryKind>('agents')
  const [entryName, setEntryName] = useState('')
  const [content, setContent] = useState(KIND_BY_ID.agents.template)
  // Plugin install source — shown only when ``kind === 'plugins'``.
  // Mirrors the trio the BE added in the group-policy cache: a
  // plugin with ``sourceUrl`` set will be installed from git on
  // the runner instead of being written to disk.
  const [sourceUrl, setSourceUrl] = useState('')
  const [sourceRef, setSourceRef] = useState('')
  const [sourceSubdir, setSourceSubdir] = useState('')
  const [sourceUrlError, setSourceUrlError] = useState<string | null>(null)
  // Agents only: which model this one runs against. Null inherits the
  // group's, then the deployment's.
  const [model, setModel] = useState<string | null>(null)
  const [nameError, setNameError] = useState<string | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])
  const [contentError, setContentError] = useState<string | null>(null)

  const showFeedback = useCallback((variant: 'success' | 'critical', message: string) => {
    setFeedbackBanner({ variant, message })
    setTimeout(() => setFeedbackBanner(null), 5000)
  }, [])

  const createMutation = useMutation({
    mutationFn: (payload: {
      kind: string
      entry_name: string
      content: string
      content_type: string
      enabled: boolean
      source_url?: string | null
      source_ref?: string | null
      source_subdir?: string | null
      model?: string | null
    }) =>
      client.post(`/portal/groups/${groupId}/entries`, payload).then((r) => r.data),
    onSuccess: (data: { warnings?: string[] }) => {
      queryClient.invalidateQueries({ queryKey: ['group-entries', groupId] })
      // Saved either way. But if something in it will not work — a tool
      // igni does not have, say — leaving the page would flash the
      // warning past somebody on their way out. Stay, and say so.
      if (data?.warnings?.length) {
        setWarnings(data.warnings)
        return
      }
      showFeedback('success', 'Entry created')
      navigate(`/groups/${groupId}?tab=entries`)
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setContentError(detail ?? 'Failed to create entry')
    },
  })

  const spec = KIND_BY_ID[kind]
  const contentType: ContentType = spec.contentType
  const knownTools = useKnownTools(kind)
  const badTools = unknownTools(content, knownTools)

  const handleKindChange = (newKind: EntryKind) => {
    if (newKind !== kind) {
      // Reload the template when switching kinds so the textarea
      // shows the right scaffold instead of stale content from the
      // previous kind.
      setContent(KIND_BY_ID[newKind].template)
      // Plugin-source fields only make sense for plugins. Clear
      // them when leaving so a non-plugin submit doesn't carry
      // stale values through to the payload.
      if (newKind !== 'agents') {
        setModel(null)
      }
      if (kind === 'plugins' || newKind === 'plugins') {
        setSourceUrl('')
        setSourceRef('')
        setSourceSubdir('')
        setSourceUrlError(null)
      }
    }
    setKind(newKind)
  }

  const loadTemplate = () => {
    setContent(spec.template)
    setContentError(null)
  }

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault()
    const trimmed = entryName.trim()
    if (!trimmed) {
      setNameError('Entry name is required')
      return
    }

    // Plugin source — when present, content is optional (the
    // server's installer fetches the plugin from the source URL).
    // When absent for plugins, fall back to writing the legacy YAML
    // content as before.
    const trimmedSourceUrl = sourceUrl.trim()
    let trimmedSourceRef: string | null = null
    let trimmedSourceSubdir: string | null = null
    if (kind === 'plugins') {
      if (trimmedSourceUrl) {
        // Mirror the BE validation: only http(s)/git/ssh schemes;
        // everything else (file://, plain paths, typo'd URLs) gets
        // rejected before it hits the wire.
        try {
          const parsed = new URL(trimmedSourceUrl)
          if (!['http:', 'https:', 'git:', 'ssh:'].includes(parsed.protocol)) {
            setSourceUrlError('Source URL must use http, https, git, or ssh')
            return
          }
        } catch {
          setSourceUrlError('Source URL is not a valid URL')
          return
        }
        const ref = sourceRef.trim()
        const subdir = sourceSubdir.trim()
        // A ref or subdir without a URL is caught server-side; we
        // short-circuit it client-side to surface a clearer error.
        if ((ref || subdir) && !trimmedSourceUrl) {
          setSourceUrlError('Source URL is required when ref or subdir are set')
          return
        }
        trimmedSourceRef = ref || null
        trimmedSourceSubdir = subdir || null
      }
    }

    if (!content.trim() && !(kind === 'plugins' && trimmedSourceUrl)) {
      setContentError('Content is required')
      return
    }
    setNameError(null)
    setContentError(null)
    setSourceUrlError(null)
    const payload: {
      kind: string
      entry_name: string
      content: string
      content_type: string
      enabled: boolean
      source_url?: string | null
      source_ref?: string | null
      source_subdir?: string | null
      model?: string | null
    } = {
      kind,
      entry_name: trimmed,
      content,
      content_type: contentType,
      enabled: true,
    }
    if (kind === 'agents' && model) {
      payload.model = model
    }
    if (kind === 'plugins') {
      payload.source_url = trimmedSourceUrl || null
      payload.source_ref = trimmedSourceRef
      payload.source_subdir = trimmedSourceSubdir
    }
    createMutation.mutate(payload)
  }

  // The group that holds what igni ships takes no additions. The
  // portal does not link here for it, but the URL is guessable and the
  // server would refuse the POST — better to say so than to let somebody
  // write an agent and lose it on submit.
  if (groupQuery.data?.is_default) {
    return (
      <Stack gap="spacious">
        <Banner
          variant="info"
          title="This is what igni ships"
          description="Nothing can be added to it. Create a group from it, and add to that instead."
        />
        <Stack direction="horizontal" gap="condensed">
          <Button as={RouterLink} to={`/groups/${groupId}?tab=entries`} variant="primary">
            Back to the group
          </Button>
          <Button as={RouterLink} to="/groups/new">
            New group from it
          </Button>
        </Stack>
      </Stack>
    )
  }

  return (
    <Stack gap="spacious">
      <Breadcrumbs>
        <Breadcrumbs.Item href="/groups">Groups</Breadcrumbs.Item>
        {groupQuery.data?.name && (
          <Breadcrumbs.Item href={`/groups/${groupId}`}>{groupQuery.data.name}</Breadcrumbs.Item>
        )}
        <Breadcrumbs.Item selected>New entry</Breadcrumbs.Item>
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
          <PageHeader.Title as="h1">New entry</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text size="medium">
            Add an agent definition, MCP server config, plugin setting, or igni setting for this group.
          </Text>
        </PageHeader.Description>
      </PageHeader>

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

      <div className="new-entry-form">
        <form onSubmit={handleSubmit}>
          <Stack direction="vertical" gap="normal">
            <FormControl>
              <FormControl.Label>Kind</FormControl.Label>
              <FormControl.Caption>{spec.description}</FormControl.Caption>
              <Select value={kind} onChange={(e) => handleKindChange(e.target.value as EntryKind)}>
                {ENTRY_KINDS.map((k) => (
                  <Select.Option key={k.id} value={k.id}>
                    {k.label}
                  </Select.Option>
                ))}
              </Select>
            </FormControl>

            <FormControl>
              <FormControl.Label>Entry name</FormControl.Label>
              <TextInput
                value={entryName}
                onChange={(e) => { setEntryName(e.currentTarget.value); setNameError(null) }}
                placeholder="e.g. code-reviewer, github-mcp"
                validationStatus={nameError ? 'error' : undefined}
                autoFocus
                block
              />
              {nameError && <FormControl.Validation variant="error">{nameError}</FormControl.Validation>}
            </FormControl>

            {kind === 'agents' && (
              <AgentModelField value={model} onChange={setModel} groupDefault={groupQuery.data?.default_model} />
            )}

            {kind === 'plugins' && (
              <Stack direction="vertical" gap="normal">
                <FormControl>
                  <FormControl.Label>Source URL</FormControl.Label>
                  <TextInput
                    value={sourceUrl}
                    onChange={(e) => { setSourceUrl(e.currentTarget.value); setSourceUrlError(null) }}
                    placeholder="https://github.com/owner/repo"
                    validationStatus={sourceUrlError ? 'error' : undefined}
                    block
                  />
                  {sourceUrlError && <FormControl.Validation variant="error">{sourceUrlError}</FormControl.Validation>}
                </FormControl>
                <FormControl>
                  <FormControl.Label>
                    Source ref <Text size="small" className="group-detail-muted">(optional: branch, tag, or SHA)</Text>
                  </FormControl.Label>
                  <TextInput
                    value={sourceRef}
                    onChange={(e) => setSourceRef(e.currentTarget.value)}
                    placeholder="main"
                    block
                  />
                </FormControl>
                <FormControl>
                  <FormControl.Label>
                    Source subdir <Text size="small" className="group-detail-muted">(optional: path inside the repo)</Text>
                  </FormControl.Label>
                  <TextInput
                    value={sourceSubdir}
                    onChange={(e) => setSourceSubdir(e.currentTarget.value)}
                    placeholder="plugins/my-plugin"
                    block
                  />
                </FormControl>
                <Text size="small" className="group-detail-muted">
                  Leave the URL empty to write the YAML content below as a legacy plugin entry. Set a URL to install the plugin from git on the runner instead.
                </Text>
              </Stack>
            )}

            <FormControl>
              <FormControl.Label>Where this lands</FormControl.Label>
              <Text size="small" className="group-detail-muted">
                <code>{spec.path}</code> on every machine in the group, as {contentType}.
              </Text>
            </FormControl>

            {badTools.length > 0 && <ToolWarning names={badTools} known={knownTools} />}

            <ContentEditor
              value={content}
              onChange={(next) => {
                setContent(next)
                setContentError(null)
              }}
              contentType={contentType}
              error={contentError}
              action={
                <Button type="button" size="small" variant="invisible" onClick={loadTemplate}>
                  Load template
                </Button>
              }
            />

            <Stack direction="horizontal" gap="condensed">
              <Button type="submit" variant="primary" disabled={createMutation.isPending}>
                {createMutation.isPending ? 'Creating…' : 'Create entry'}
              </Button>
              <Button as={RouterLink} to={`/groups/${groupId}?tab=entries`} disabled={createMutation.isPending}>
                Cancel
              </Button>
            </Stack>
          </Stack>
        </form>
      </div>
    </Stack>
  )
}
