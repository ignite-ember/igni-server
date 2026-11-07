import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ActionList,
  ActionMenu,
  Banner,
  Button,
  FormControl,
  Heading,
  Label,
  PageHeader,
  Stack,
  Text,
  TextInput,
} from '@primer/react'
import { Blankslate, DataTable, Table } from '@primer/react/experimental'
import { CheckIcon, CopyIcon, KeyAsteriskIcon } from '@primer/octicons-react'
import client from '../api/client'
import { refusalOf } from '../api/refusal'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import type { ApiKey, ApiKeyLifetime, CreatedApiKey, TeamMemberList, UserMe } from '../types'

const LIFETIMES: ApiKeyLifetime[] = [30, 90, 365]

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString() : 'never'
}

/**
 * The key just made, shown once.
 *
 * A panel that stays, not a toast: the person has to copy it before it is
 * gone for good — igni server keeps only a hash of it — so it stays until
 * they dismiss it or make another.
 */
function NewKey({ created, onDismiss }: { created: CreatedApiKey; onDismiss: () => void }) {
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    await navigator.clipboard.writeText(created.api_key)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <Banner
      variant="warning"
      title={`Copy “${created.name}” now`}
      onDismiss={onDismiss}
      description={
        <Stack gap="condensed">
          <Text size="small">
            This is the only time the key is shown. Give it to the program that needs it; anyone holding it can act as
            you until it expires or you revoke it.
          </Text>
          <Stack direction="horizontal" gap="condensed" align="center" wrap="wrap">
            <TextInput value={created.api_key} readOnly monospace aria-label="Your new API key" block />
            <Button leadingVisual={copied ? CheckIcon : CopyIcon} onClick={copy}>
              {copied ? 'Copied' : 'Copy key'}
            </Button>
          </Stack>
        </Stack>
      }
    />
  )
}

function NewKeyForm({ onCreated, onError }: { onCreated: (key: CreatedApiKey) => void; onError: (message: string) => void }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [days, setDays] = useState<ApiKeyLifetime>(90)
  const [nameError, setNameError] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: (body: { name: string; expires_in_days: ApiKeyLifetime }) =>
      client.post<CreatedApiKey>('/portal/api-keys', body).then((r) => r.data),
    onSuccess: (key) => {
      queryClient.invalidateQueries({ queryKey: ['api-keys'] })
      setName('')
      onCreated(key)
    },
    onError: (error) => onError(refusalOf(error)),
  })

  const submit = () => {
    if (!name.trim()) {
      setNameError('Say which program it is for, so you can tell keys apart.')
      return
    }
    create.mutate({ name: name.trim(), expires_in_days: days })
  }

  return (
    <Stack padding="normal" gap="normal">
      <Heading as="h2" variant="small">
        New key
      </Heading>
      <Stack direction="horizontal" gap="normal" align="end" wrap="wrap">
        <FormControl required>
          <FormControl.Label>What it is for</FormControl.Label>
          <TextInput
            placeholder="Nightly review job"
            value={name}
            maxLength={100}
            onChange={(e) => {
              setName(e.target.value)
              if (nameError) setNameError(null)
            }}
            validationStatus={nameError ? 'error' : undefined}
          />
          {nameError && <FormControl.Validation variant="error">{nameError}</FormControl.Validation>}
        </FormControl>
        <FormControl>
          <FormControl.Label>Expires after</FormControl.Label>
          <ActionMenu>
            <ActionMenu.Button>{days} days</ActionMenu.Button>
            <ActionMenu.Overlay>
              <ActionList selectionVariant="single">
                {LIFETIMES.map((lifetime) => (
                  <ActionList.Item key={lifetime} selected={days === lifetime} onSelect={() => setDays(lifetime)}>
                    {lifetime} days
                  </ActionList.Item>
                ))}
              </ActionList>
            </ActionMenu.Overlay>
          </ActionMenu>
        </FormControl>
        <Button
          variant="primary"
          leadingVisual={KeyAsteriskIcon}
          onClick={submit}
          loading={create.isPending}
          disabled={create.isPending}
        >
          Make key
        </Button>
      </Stack>
    </Stack>
  )
}

/**
 * Personal API keys: your access, handed to a program.
 *
 * A program exchanges a key for a 15-minute session as you — igni server's
 * API, and your igni in the cloud through igni-client — that cannot make
 * further keys. Yours, or — for an org admin — everybody's in the
 * organisation, each revocable.
 */
export function ApiKeysPage() {
  useDocumentTitle('API keys')
  const queryClient = useQueryClient()
  const [created, setCreated] = useState<CreatedApiKey | null>(null)
  const [feedback, setFeedback] = useState<{ ok: boolean; message: string } | null>(null)

  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  })
  const isAdmin = me.data?.role === 'admin'
  const keys = useQuery({
    queryKey: ['api-keys'],
    queryFn: () => client.get<{ keys: ApiKey[] }>('/portal/api-keys').then((r) => r.data.keys),
  })
  const team = useQuery({
    queryKey: ['team'],
    queryFn: () => client.get<TeamMemberList>('/portal/team').then((r) => r.data),
    enabled: isAdmin,
  })
  const revoke = useMutation({
    mutationFn: (keyId: string) => client.delete(`/portal/api-keys/${keyId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['api-keys'] })
      setFeedback({ ok: true, message: 'Revoked. The program holding it gets no new sessions.' })
    },
    onError: (error) => setFeedback({ ok: false, message: refusalOf(error) }),
  })

  const ownerOf = (key: ApiKey): string => {
    if (key.user_id === me.data?.user_id) return 'You'
    const person = team.data?.members.find((m) => m.user_id === key.user_id)
    return person?.full_name || person?.email || key.user_id
  }

  const list = keys.data ?? []

  return (
    <Stack gap="spacious">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.LeadingVisual>
            <KeyAsteriskIcon />
          </PageHeader.LeadingVisual>
          <PageHeader.Title>API keys</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text>
            Give a program your access: it exchanges the key for a short session as you, and can do what you can — except
            make more keys. Keys expire, and stop when you sign out everywhere or leave the organisation.
          </Text>
        </PageHeader.Description>
      </PageHeader>

      {created && <NewKey created={created} onDismiss={() => setCreated(null)} />}
      {feedback && (
        <Banner
          variant={feedback.ok ? 'success' : 'critical'}
          title={feedback.ok ? 'Done' : 'Not done'}
          description={feedback.message}
          onDismiss={() => setFeedback(null)}
        />
      )}

      <NewKeyForm
        onCreated={(key) => {
          setFeedback(null)
          setCreated(key)
        }}
        onError={(message) => setFeedback({ ok: false, message })}
      />

      {list.length === 0 && !keys.isLoading ? (
        <Blankslate>
          <Blankslate.Heading>No API keys</Blankslate.Heading>
          <Blankslate.Description>Make one above for a script, a CI job or anything else that acts as you.</Blankslate.Description>
        </Blankslate>
      ) : (
        <Table.Container>
          <DataTable
            aria-label="API keys"
            data={list.map((key) => ({ id: key.key_id, ...key }))}
            columns={[
              { header: 'Name', field: 'name', rowHeader: true },
              ...(isAdmin ? [{ header: 'Whose', field: 'user_id' as const, renderCell: (row: ApiKey) => ownerOf(row) }] : []),
              { header: 'Made', field: 'created_at', renderCell: (row) => when(row.created_at) },
              { header: 'Last used', field: 'last_used_at', renderCell: (row) => when(row.last_used_at) },
              {
                header: 'Expires',
                field: 'expires_at',
                renderCell: (row) =>
                  row.expired ? <Label variant="danger">expired</Label> : when(row.expires_at),
              },
              {
                header: '',
                field: 'key_id',
                renderCell: (row) => (
                  <Button
                    size="small"
                    variant="danger"
                    onClick={() => revoke.mutate(row.key_id)}
                    aria-label={`Revoke the key “${row.name}”`}
                  >
                    Revoke
                  </Button>
                ),
              },
            ]}
          />
        </Table.Container>
      )}
    </Stack>
  )
}
