import { useState } from 'react'
import { useParams, useNavigate, Link as RouterLink } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Banner, Breadcrumbs, Button, FormControl, PageHeader, Select, Stack, Text, TextInput } from '@primer/react'
import { ArrowLeftIcon } from '@primer/octicons-react'
import client from '../api/client'
import { refusalOf } from '../api/refusal'
import { unknownTools } from '../lib/agentTools'
import { ENTRY_KINDS, KIND_BY_ID } from '../lib/entryKinds'
import {
  checkNewEntry,
  INITIAL_NEW_ENTRY_FORM,
  NO_NEW_ENTRY_ERRORS,
  withKind,
  type NewEntryErrors,
  type NewEntryField,
  type NewEntryForm,
} from '../lib/newEntryForm'
import { useKnownTools } from '../hooks/useKnownTools'
import { AgentModelField } from '../components/AgentModelField'
import { ContentEditor } from '../components/ContentEditor'
import { PluginSourceFields } from '../components/entry/PluginSourceFields'
import { ShippedGroupNotice } from '../components/entry/ShippedGroupNotice'
import { ToolWarning } from '../components/entry/ToolWarning'
import type { CreateOrgGroupEntryRequest, EntryKind, OrgGroupDetail, OrgGroupEntryList } from '../types'
import './cards.css'

export function NewEntryPage() {
  const { id: groupId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const entriesPath = `/groups/${groupId}?tab=entries`

  // For the breadcrumb, the shipped-group guard, and the model field,
  // which shows what an agent inherits when it names none of its own.
  const groupQuery = useQuery({
    queryKey: ['group', groupId],
    queryFn: () => client.get<OrgGroupDetail>(`/portal/groups/${groupId}`).then((r) => r.data),
    enabled: !!groupId,
  })

  const [form, setForm] = useState<NewEntryForm>(INITIAL_NEW_ENTRY_FORM)
  const [errors, setErrors] = useState<NewEntryErrors>(NO_NEW_ENTRY_ERRORS)
  const [warnings, setWarnings] = useState<string[]>([])

  const edit = (changes: Partial<NewEntryForm>, clears?: NewEntryField) => {
    setForm((current) => ({ ...current, ...changes }))
    if (clears) setErrors((current) => ({ ...current, [clears]: null }))
  }

  const createMutation = useMutation({
    mutationFn: (request: CreateOrgGroupEntryRequest) =>
      client.post<OrgGroupEntryList>(`/portal/groups/${groupId}/entries`, request).then((r) => r.data),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['group-entries', groupId] })
      // Saved either way. But if something in it will not work — a tool
      // igni does not have, say — leaving the page would flash the
      // warning past somebody on their way out. Stay, and say so.
      if (data.warnings?.length) {
        setWarnings(data.warnings)
        return
      }
      navigate(entriesPath)
    },
    onError: (err) => setErrors((current) => ({ ...current, content: refusalOf(err, 'Failed to create entry') })),
  })

  const { kind, content } = form
  const spec = KIND_BY_ID[kind]
  const knownTools = useKnownTools(kind)
  const badTools = unknownTools(content, knownTools)

  const handleKindChange = (next: EntryKind) => {
    if (kind === 'plugins' || next === 'plugins') setErrors((current) => ({ ...current, sourceUrl: null }))
    setForm((current) => withKind(current, next))
  }

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault()
    const check = checkNewEntry(form)
    if (!check.ok) {
      setErrors((current) => ({ ...current, [check.field]: check.message }))
      return
    }
    setErrors(NO_NEW_ENTRY_ERRORS)
    createMutation.mutate(check.request)
  }

  if (groupQuery.data?.is_default) return <ShippedGroupNotice groupId={groupId} />

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
          <Button variant="invisible" as={RouterLink} to={entriesPath} aria-label="Back to entries">
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
                value={form.entryName}
                onChange={(e) => edit({ entryName: e.currentTarget.value }, 'name')}
                placeholder="e.g. code-reviewer, github-mcp"
                validationStatus={errors.name ? 'error' : undefined}
                autoFocus
                block
              />
              {errors.name && <FormControl.Validation variant="error">{errors.name}</FormControl.Validation>}
            </FormControl>

            {kind === 'agents' && (
              <AgentModelField
                value={form.model}
                onChange={(model) => edit({ model })}
                groupDefault={groupQuery.data?.default_model}
              />
            )}

            {kind === 'plugins' && (
              <PluginSourceFields
                value={form.pluginSource}
                onChange={(pluginSource) =>
                  edit({ pluginSource }, pluginSource.url !== form.pluginSource.url ? 'sourceUrl' : undefined)
                }
                urlError={errors.sourceUrl}
              />
            )}

            <FormControl>
              <FormControl.Label>Where this lands</FormControl.Label>
              <Text size="small" className="group-detail-muted">
                <code>{spec.path}</code> on every machine in the group, as {spec.contentType}.
              </Text>
            </FormControl>

            {badTools.length > 0 && <ToolWarning names={badTools} known={knownTools} />}

            <ContentEditor
              value={content}
              onChange={(next) => edit({ content: next }, 'content')}
              contentType={spec.contentType}
              error={errors.content}
              action={
                <Button
                  type="button"
                  size="small"
                  variant="invisible"
                  onClick={() => edit({ content: spec.template }, 'content')}
                >
                  Load template
                </Button>
              }
            />

            <Stack direction="horizontal" gap="condensed">
              <Button type="submit" variant="primary" disabled={createMutation.isPending}>
                {createMutation.isPending ? 'Creating…' : 'Create entry'}
              </Button>
              <Button as={RouterLink} to={entriesPath} disabled={createMutation.isPending}>
                Cancel
              </Button>
            </Stack>
          </Stack>
        </form>
      </div>
    </Stack>
  )
}
