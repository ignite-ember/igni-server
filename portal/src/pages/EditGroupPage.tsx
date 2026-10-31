import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams, Link as RouterLink } from 'react-router-dom'
import {
  Banner,
  Breadcrumbs,
  Button,
  FormControl,
  PageHeader,
  Stack,
  Text,
  TextInput,
  Textarea,
} from '@primer/react'
import { ArrowLeftIcon } from '@primer/octicons-react'

import client from '../api/client'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import type { GroupTheme, OrgGroupDetail } from '../types'

/** Matches the server's cap in ``GroupTheme`` — reject here so the
 *  admin finds out while looking at the picker, not after a save. */
const MAX_BRAND_MARK_BYTES = 64 * 1024

/** What the server's ``BRAND_MARK_MIME_PATTERN`` accepts. */
const BRAND_MARK_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/svg+xml']

/**
 * One optional colour.
 *
 * A native colour input can't express "unset", so clearing is its own
 * control. Unset matters: it is the difference between "this group
 * wants igni's red for errors" and "this group has opinions about
 * every colour in the product".
 */
function ColourField({
  label,
  caption,
  value,
  onChange,
}: {
  label: string
  caption: string
  value: string | null | undefined
  onChange: (next: string | null) => void
}) {
  return (
    <FormControl>
      <FormControl.Label>{label}</FormControl.Label>
      <FormControl.Caption>{caption}</FormControl.Caption>
      <Stack direction="horizontal" gap="condensed" align="center">
        <input
          type="color"
          aria-label={label}
          value={value ?? '#f97316'}
          onChange={(e) => onChange(e.currentTarget.value)}
          style={{ width: 48, height: 32, padding: 0, border: 'none', background: 'none' }}
        />
        <Text size="small" className="group-detail-muted">
          {value ?? "igni's default"}
        </Text>
        {value && (
          <Button size="small" variant="invisible" onClick={() => onChange(null)}>
            Clear
          </Button>
        )}
      </Stack>
    </FormControl>
  )
}

/**
 * A group's name and description, on their own page.
 *
 * This was a dialog with one field in it. A dialog is for a decision you
 * make and dismiss; renaming a team and writing down what it is for is
 * neither — it wants room, and it wants the description to be more than
 * a line somebody dashes off because the box was small.
 */
export function EditGroupPage() {
  const { id: groupId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  useDocumentTitle('Edit group')

  const groupQuery = useQuery({
    queryKey: ['group', groupId],
    queryFn: () => client.get<OrgGroupDetail>(`/portal/groups/${groupId}`).then((r) => r.data),
  })

  const group = groupQuery.data
  const [name, setName] = useState<string | null>(null)
  const [description, setDescription] = useState<string | null>(null)
  const [theme, setTheme] = useState<GroupTheme | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Null until edited, so the fields show what the group says without
  // an effect to seed them.
  const nameValue = name ?? group?.name ?? ''
  const descriptionValue = description ?? group?.description ?? ''
  const themeValue = theme ?? group?.theme ?? {}

  const setThemeField = (key: keyof GroupTheme, next: string | null) => {
    setTheme({ ...themeValue, [key]: next })
    setError(null)
  }

  const save = useMutation({
    mutationFn: () =>
      client
        .patch(`/portal/groups/${groupId}`, {
          name: nameValue.trim(),
          description: descriptionValue.trim() || null,
          // Always sent, because the form always shows the current
          // theme. A theme with every field cleared is how the server
          // is told to drop branding entirely — it stores NULL, which
          // is what "no overrides" already meant.
          theme: Object.fromEntries(
            Object.entries(themeValue).filter(([, v]) => v),
          ) as GroupTheme,
        })
        .then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['group', groupId] })
      queryClient.invalidateQueries({ queryKey: ['groups'] })
      navigate(`/groups/${groupId}`)
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail ?? 'Could not save the group')
    },
  })

  if (groupQuery.isLoading) {
    return (
      <Stack gap="spacious" padding="normal">
        <Text size="small" className="group-detail-muted">
          Loading…
        </Text>
      </Stack>
    )
  }

  if (groupQuery.isError || !group) {
    return (
      <Stack gap="spacious">
        <Banner variant="critical" title="Group not found" />
        <Button as={RouterLink} to="/groups">
          <ArrowLeftIcon /> Back to groups
        </Button>
      </Stack>
    )
  }

  // The group holding what igni ships takes no edits at all; the server
  // refuses them, and a form that cannot be saved is worse than none.
  if (group.is_default) {
    return (
      <Stack gap="spacious">
        <Banner
          variant="info"
          title="This is what igni ships"
          description="Its name and description are fixed. Create a group from it to make your own version."
        />
        <Stack direction="horizontal" gap="condensed">
          <Button as={RouterLink} to={`/groups/${groupId}`} variant="primary">
            Back to the group
          </Button>
          <Button as={RouterLink} to="/groups/new">
            New group from it
          </Button>
        </Stack>
      </Stack>
    )
  }

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault()
    if (!nameValue.trim()) {
      setError('A group needs a name')
      return
    }
    setError(null)
    save.mutate()
  }

  return (
    <Stack gap="spacious">
      <Breadcrumbs>
        <Breadcrumbs.Item href="/groups">Groups</Breadcrumbs.Item>
        <Breadcrumbs.Item href={`/groups/${groupId}`}>{group.name}</Breadcrumbs.Item>
        <Breadcrumbs.Item selected>Edit</Breadcrumbs.Item>
      </Breadcrumbs>

      <PageHeader>
        <PageHeader.TitleArea>
          <Button
            variant="invisible"
            as={RouterLink}
            to={`/groups/${groupId}`}
            aria-label="Back to the group"
          >
            <ArrowLeftIcon />
          </Button>
          <PageHeader.Title as="h1">Edit {group.name}</PageHeader.Title>
        </PageHeader.TitleArea>
      </PageHeader>

      {error && <Banner variant="critical" title={error} onDismiss={() => setError(null)} />}

      <form onSubmit={handleSubmit}>
        <Stack direction="vertical" gap="normal" style={{ maxWidth: 720 }}>
          <FormControl>
            <FormControl.Label>Name</FormControl.Label>
            <FormControl.Caption>
              What members see, and what other groups copy from when they start from this one.
            </FormControl.Caption>
            <TextInput
              value={nameValue}
              onChange={(e) => {
                setName(e.currentTarget.value)
                setError(null)
              }}
              block
              autoFocus
            />
          </FormControl>

          <FormControl>
            <FormControl.Label>Description</FormControl.Label>
            <FormControl.Caption>
              What this group is for, who belongs in it, and why it holds what it holds. Members do
              not see this — it is here so the next admin does not have to guess.
            </FormControl.Caption>
            <Textarea
              value={descriptionValue}
              onChange={(e) => setDescription(e.currentTarget.value)}
              rows={12}
              resize="vertical"
              block
            />
          </FormControl>

          <Text weight="semibold" size="medium">
            Branding
          </Text>
          <Text size="small" className="group-detail-muted">
            What this group's people see in the igni app, VS Code and JetBrains. Anything left
            unset stays igni's. Light and dark still follow each person's own preference — these
            colours are defined for both.
          </Text>

          <FormControl>
            <FormControl.Label>Brand name</FormControl.Label>
            <FormControl.Caption>
              Replaces the igni wordmark in the client header. Leave empty to keep igni.
            </FormControl.Caption>
            <TextInput
              value={themeValue.brand_name ?? ''}
              onChange={(e) => setThemeField('brand_name', e.currentTarget.value || null)}
              placeholder="igni"
              block
            />
          </FormControl>

          <FormControl>
            <FormControl.Label>Brand mark</FormControl.Label>
            <FormControl.Caption>
              PNG, JPEG, WebP or SVG, up to 64 KB. Stored with the group and sent to clients
              inline, so it works offline and on a private network — there is no image URL to
              fetch.
            </FormControl.Caption>
            <Stack direction="horizontal" gap="condensed" align="center">
              {themeValue.brand_mark && (
                <img
                  src={themeValue.brand_mark}
                  alt="Brand mark preview"
                  style={{ width: 32, height: 32, objectFit: 'contain' }}
                />
              )}
              <input
                type="file"
                accept={BRAND_MARK_TYPES.join(',')}
                onChange={(e) => {
                  const file = e.currentTarget.files?.[0]
                  if (!file) return
                  if (!BRAND_MARK_TYPES.includes(file.type)) {
                    setError('A brand mark must be a PNG, JPEG, WebP or SVG.')
                    return
                  }
                  // Base64 inflates by about a third, and the server
                  // measures the encoded string — so check against the
                  // budget the server will actually apply.
                  if (file.size * 1.37 > MAX_BRAND_MARK_BYTES) {
                    setError('That image is over the 64 KB limit once encoded.')
                    return
                  }
                  const reader = new FileReader()
                  reader.onload = () => setThemeField('brand_mark', String(reader.result))
                  reader.onerror = () => setError('Could not read that file.')
                  reader.readAsDataURL(file)
                }}
              />
              {themeValue.brand_mark && (
                <Button
                  size="small"
                  variant="invisible"
                  onClick={() => setThemeField('brand_mark', null)}
                >
                  Remove
                </Button>
              )}
            </Stack>
          </FormControl>

          <ColourField
            label="Accent"
            caption="The primary brand colour — buttons, links, the header gradient. Hover states are derived from it, so there is nothing separate to set."
            value={themeValue.accent}
            onChange={(v) => setThemeField('accent', v)}
          />
          <ColourField
            label="Danger"
            caption="Errors and destructive actions. Most groups leave this alone — red carries meaning."
            value={themeValue.danger}
            onChange={(v) => setThemeField('danger', v)}
          />
          <ColourField
            label="Success"
            caption="Completed runs and confirmations."
            value={themeValue.success}
            onChange={(v) => setThemeField('success', v)}
          />

          <Stack direction="horizontal" gap="condensed">
            <Button type="submit" variant="primary" disabled={save.isPending}>
              {save.isPending ? 'Saving…' : 'Save'}
            </Button>
            <Button as={RouterLink} to={`/groups/${groupId}`} disabled={save.isPending}>
              Cancel
            </Button>
          </Stack>
        </Stack>
      </form>
    </Stack>
  )
}
