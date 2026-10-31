import { useState } from 'react'
import { useNavigate, Link as RouterLink } from 'react-router-dom'
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
  Textarea,
} from '@primer/react'
import client from '../api/client'
import type { OrgGroupList } from '../types'
import './NewGroupPage.css'

export function NewGroupPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [nameError, setNameError] = useState<string | null>(null)
  // Which group to copy, or '' for an empty one. Any group can be the
  // template; the Default one holds what igni ships.
  const [templateId, setTemplateId] = useState('')

  const groupsQuery = useQuery({
    queryKey: ['groups', 'templates'],
    queryFn: () => client.get<OrgGroupList>('/portal/groups').then((r) => r.data),
  })

  const createMutation = useMutation({
    mutationFn: (payload: { name: string; description?: string; template_group_id?: string }) =>
      client.post('/portal/groups', payload).then((r) => r.data),
    onSuccess: (group: { id: string; name: string }) => {
      queryClient.invalidateQueries({ queryKey: ['groups'] })
      navigate(`/groups/${group.id}`)
    },
    onError: (err: unknown) => {
      const e = err as { response?: { data?: { detail?: string } } }
      const detail = e.response?.data?.detail
      setNameError(detail ?? 'Failed to create group')
    },
  })

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) {
      setNameError('Group name is required')
      return
    }
    createMutation.mutate({
      name: trimmed,
      description: description.trim() || undefined,
      template_group_id: templateId || undefined,
    })
  }

  return (
    <Stack gap="spacious">
      <Breadcrumbs>
        <Breadcrumbs.Item href="/groups">Groups</Breadcrumbs.Item>
        <Breadcrumbs.Item selected>New group</Breadcrumbs.Item>
      </Breadcrumbs>

      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.Title as="h1">New group</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text size="medium">
            A group is exactly what its members get: its agents, MCP servers and plugins, and nothing else.
          </Text>
        </PageHeader.Description>
      </PageHeader>

      {createMutation.isError && (
        <Banner
          variant="critical"
          title="Failed to create group"
          description={(createMutation.error as Error)?.message ?? 'Please try again.'}
        />
      )}

      <div className="new-group-form">
        <form onSubmit={handleSubmit}>
          <Stack direction="vertical" gap="normal">
            <FormControl>
              <FormControl.Label>Name</FormControl.Label>
              <TextInput
                value={name}
                onChange={(e) => { setName(e.currentTarget.value); setNameError(null) }}
                placeholder="e.g. contractors, engineering, qa"
                validationStatus={nameError ? 'error' : undefined}
                autoFocus
                block
              />
              {nameError && <FormControl.Validation variant="error">{nameError}</FormControl.Validation>}
            </FormControl>
            <FormControl>
              <FormControl.Label>
                Description <Text size="small" className="new-group-muted">(optional)</Text>
              </FormControl.Label>
              <Textarea
                value={description}
                onChange={(e) => setDescription(e.currentTarget.value)}
                placeholder="What is this group for?"
                rows={5}
                resize="vertical"
                block
              />
            </FormControl>
            <FormControl>
              <FormControl.Label>Start from</FormControl.Label>
              <FormControl.Caption>
                A group is exactly what its members get. Copying an existing one is how you say
                &quot;the same as that team, then adjust&quot; — the Default group holds what igni ships.
              </FormControl.Caption>
              <Select value={templateId} onChange={(e) => setTemplateId(e.target.value)} block>
                <Select.Option value="">Empty group</Select.Option>
                {(groupsQuery.data?.groups ?? []).map((group) => (
                  <Select.Option key={group.id} value={group.id}>
                    Copy of {group.name}
                    {group.is_default ? ' — what igni ships' : ''}
                    {group.entry_count ? ` (${group.entry_count} entries)` : ''}
                  </Select.Option>
                ))}
              </Select>
            </FormControl>
            <Stack direction="horizontal" gap="condensed">
              <Button
                type="submit"
                variant="primary"
                disabled={createMutation.isPending}
              >
                {createMutation.isPending ? 'Creating…' : 'Create group'}
              </Button>
              <Button
                as={RouterLink}
                to="/groups"
                disabled={createMutation.isPending}
              >
                Cancel
              </Button>
            </Stack>
          </Stack>
        </form>
      </div>
    </Stack>
  )
}
