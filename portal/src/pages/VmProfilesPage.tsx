import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import {
  Banner,
  Button,
  Checkbox,
  FormControl,
  Label,
  PageHeader,
  Select,
  Stack,
  Text,
  Textarea,
  TextInput,
} from '@primer/react'
import { Blankslate, DataTable, Table } from '@primer/react/experimental'
import { ContainerIcon } from '@primer/octicons-react'
import client from '../api/client'
import { refusalOf } from '../api/refusal'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import type { ProducerAssignment, UserMe, VmProfile, VmProfileSettings, VmProfilesOverview } from '../types'

const BLANK: VmProfileSettings = {
  name: '',
  memory_mib: 4096,
  normal_memory_mib: 1024,
  vcpus: 1,
  idle_before_pause_s: 10,
  expected_memory_mib: null,
  packages: { apt: [], pip: [], npm: [] },
  setup_script: null,
  erase_after_idle_days: null,
}

/** One package per line or separated by spaces — how people paste a list. */
function packageList(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((p) => p.trim())
    .filter(Boolean)
}

function idleLabel(seconds: number | null): string {
  return seconds === null ? 'never' : `${seconds} s`
}

/** How a profile treats an app's VM nobody wakes. */
function idleErasureLabel(days: number | null | undefined): string {
  return days == null ? 'Kept' : `Erased after ${days} ${days === 1 ? 'day' : 'days'} unused`
}

function ProfileForm({
  initial,
  onSave,
  onCancel,
  saving,
  maxIdleDays,
}: {
  initial: VmProfileSettings
  onSave: (settings: VmProfileSettings) => void
  onCancel: () => void
  saving: boolean
  /** The platform's longest `erase_after_idle_days`; null: none. */
  maxIdleDays: number | null
}) {
  const [name, setName] = useState(initial.name)
  const [memory, setMemory] = useState(String(initial.memory_mib))
  const [normal, setNormal] = useState(String(initial.normal_memory_mib))
  const [vcpus, setVcpus] = useState(String(initial.vcpus))
  const [never, setNever] = useState(initial.idle_before_pause_s === null)
  const [idle, setIdle] = useState(String(initial.idle_before_pause_s ?? 600))
  const [expected, setExpected] = useState(initial.expected_memory_mib === null ? '' : String(initial.expected_memory_mib))
  const [apt, setApt] = useState(initial.packages.apt.join('\n'))
  const [pip, setPip] = useState(initial.packages.pip.join('\n'))
  const [npm, setNpm] = useState(initial.packages.npm.join('\n'))
  const [script, setScript] = useState(initial.setup_script ?? '')
  const [eraseDays, setEraseDays] = useState(
    initial.erase_after_idle_days == null ? '' : String(initial.erase_after_idle_days),
  )

  return (
    <form
      aria-label="VM profile"
      onSubmit={(e) => {
        e.preventDefault()
        onSave({
          name: name.trim(),
          memory_mib: Number(memory),
          normal_memory_mib: Number(normal),
          vcpus: Number(vcpus),
          idle_before_pause_s: never ? null : Number(idle),
          expected_memory_mib: expected.trim() ? Number(expected) : null,
          packages: { apt: packageList(apt), pip: packageList(pip), npm: packageList(npm) },
          setup_script: script.trim() ? script : null,
          erase_after_idle_days: eraseDays.trim() ? Number(eraseDays) : null,
        })
      }}
    >
      <Stack gap="normal">
        <Stack direction="horizontal" gap="normal" wrap="wrap">
          <FormControl required>
            <FormControl.Label>Name</FormControl.Label>
            <TextInput value={name} onChange={(e) => setName(e.target.value)} />
          </FormControl>
          <FormControl required>
            <FormControl.Label>Normal memory (MiB)</FormControl.Label>
            <TextInput type="number" min={128} value={normal} onChange={(e) => setNormal(e.target.value)} />
          </FormControl>
          <FormControl required>
            <FormControl.Label>Most memory (MiB)</FormControl.Label>
            <TextInput type="number" min={128} value={memory} onChange={(e) => setMemory(e.target.value)} />
            <FormControl.Caption>Given while the VM needs more than its normal memory, when the node has room.</FormControl.Caption>
          </FormControl>
          <FormControl required>
            <FormControl.Label>vCPUs</FormControl.Label>
            <TextInput type="number" min={1} value={vcpus} onChange={(e) => setVcpus(e.target.value)} />
          </FormControl>
          <FormControl disabled={never}>
            <FormControl.Label>Idle before suspend (s)</FormControl.Label>
            <TextInput type="number" min={0} value={idle} onChange={(e) => setIdle(e.target.value)} />
          </FormControl>
        </Stack>
        <FormControl>
          <Checkbox checked={never} onChange={(e) => setNever(e.target.checked)} />
          <FormControl.Label>Never suspend</FormControl.Label>
        </FormControl>
        <FormControl>
          <FormControl.Label>Expected memory (MiB)</FormControl.Label>
          <TextInput type="number" min={128} value={expected} onChange={(e) => setExpected(e.target.value)} />
          <FormControl.Caption>What a VM is expected to use, for placing it on a node. Empty: the node&apos;s own estimate.</FormControl.Caption>
        </FormControl>
        <FormControl required={maxIdleDays !== null}>
          <FormControl.Label>Erase an app&apos;s VM after this many days unused</FormControl.Label>
          <TextInput
            type="number"
            min={1}
            max={maxIdleDays ?? 3650}
            value={eraseDays}
            placeholder="Never"
            onChange={(e) => setEraseDays(e.target.value)}
          />
          <FormControl.Caption>
            Apps and their spaces only. If an app&apos;s VM has not been woken for this many days, its memory,
            snapshots, volume and keys are erased; the app stays and its next wake starts fresh. A person&apos;s VM is
            never erased this way while they are a member. A legal hold on the app stops it. Empty: never.
            {maxIdleDays !== null && ` This platform requires at most ${maxIdleDays} days.`}
          </FormControl.Caption>
        </FormControl>
        <Stack direction="horizontal" gap="normal" wrap="wrap">
          <FormControl>
            <FormControl.Label>apt packages</FormControl.Label>
            <Textarea value={apt} onChange={(e) => setApt(e.target.value)} rows={3} />
          </FormControl>
          <FormControl>
            <FormControl.Label>pip packages</FormControl.Label>
            <Textarea value={pip} onChange={(e) => setPip(e.target.value)} rows={3} />
          </FormControl>
          <FormControl>
            <FormControl.Label>npm packages</FormControl.Label>
            <Textarea value={npm} onChange={(e) => setNpm(e.target.value)} rows={3} />
          </FormControl>
        </Stack>
        <FormControl>
          <FormControl.Label>Setup script</FormControl.Label>
          <FormControl.Caption>Runs as root when the profile&apos;s layer is built, after the packages.</FormControl.Caption>
          <Textarea value={script} onChange={(e) => setScript(e.target.value)} rows={4} block />
        </FormControl>
        <Stack direction="horizontal" gap="condensed">
          <Button type="submit" variant="primary" disabled={!name.trim() || saving}>
            {saving ? 'Saving…' : 'Save profile'}
          </Button>
          <Button onClick={onCancel}>Cancel</Button>
        </Stack>
      </Stack>
    </form>
  )
}

function AssignmentTable({
  label,
  rows,
  profiles,
  onAssign,
}: {
  label: string
  rows: ProducerAssignment[]
  profiles: VmProfile[]
  onAssign: (id: string, profileId: string | null) => void
}) {
  if (rows.length === 0) return <Text size="small">None yet.</Text>
  return (
    <Table.Container>
      <DataTable
        aria-label={label}
        data={rows}
        columns={[
          { header: 'Name', field: 'name', rowHeader: true },
          {
            header: 'VM profile',
            field: 'vm_profile_id',
            renderCell: (row) => (
              <Select
                aria-label={`VM profile of ${row.name}`}
                value={row.vm_profile_id ?? ''}
                onChange={(e) => onAssign(row.id, e.target.value || null)}
              >
                <Select.Option value="">Default</Select.Option>
                {profiles
                  .filter((p) => !p.is_default)
                  .map((p) => (
                    <Select.Option key={p.id} value={p.id}>
                      {p.name}
                    </Select.Option>
                  ))}
              </Select>
            ),
          },
        ]}
      />
    </Table.Container>
  )
}

/**
 * VM profiles: what each producer's microVM gets in the cloud. An org
 * admin defines them and gives one to each group and app; a person gets
 * their group's. A super admin may open any organisation's with `?org=`.
 */
export function VmProfilesPage() {
  useDocumentTitle('VM profiles')
  const queryClient = useQueryClient()
  const [params] = useSearchParams()
  const [editing, setEditing] = useState<VmProfile | 'new' | null>(null)
  const [feedback, setFeedback] = useState<{ variant: 'success' | 'critical'; message: string } | null>(null)

  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  })
  const orgId = params.get('org') ?? me.data?.org_id ?? ''
  const overview = useQuery({
    queryKey: ['vm-profiles', orgId],
    queryFn: () => client.get<VmProfilesOverview>(`/portal/orgs/${orgId}/vm-profiles`).then((r) => r.data),
    enabled: !!orgId,
  })
  const refresh = (message: string) => {
    queryClient.invalidateQueries({ queryKey: ['vm-profiles', orgId] })
    setFeedback({ variant: 'success', message })
  }
  const failed = (error: unknown) => setFeedback({ variant: 'critical', message: refusalOf(error) })

  const save = useMutation({
    mutationFn: (settings: VmProfileSettings) =>
      editing && editing !== 'new'
        ? client.put(`/portal/orgs/${orgId}/vm-profiles/${editing.id}`, settings)
        : client.post(`/portal/orgs/${orgId}/vm-profiles`, settings),
    onSuccess: () => {
      setEditing(null)
      refresh('Profile saved')
    },
    onError: failed,
  })
  const remove = useMutation({
    mutationFn: (profileId: string) => client.delete(`/portal/orgs/${orgId}/vm-profiles/${profileId}`),
    onSuccess: () => refresh('Profile deleted; its groups and apps are back on the default'),
    onError: failed,
  })
  const assign = useMutation({
    mutationFn: ({ kind, id, profileId }: { kind: 'groups' | 'apps'; id: string; profileId: string | null }) =>
      kind === 'groups'
        ? client.put(`/portal/orgs/${orgId}/groups/${id}/vm-profile`, { profile_id: profileId })
        : client.put(`/portal/orgs/${orgId}/apps/${id}/vm-profile`, { profile_id: profileId }),
    onSuccess: () => refresh('Assignment saved'),
    onError: failed,
  })

  if (overview.isError) {
    return (
      <Blankslate>
        <Blankslate.Heading>Organisation admins only</Blankslate.Heading>
        <Blankslate.Description>{refusalOf(overview.error, 'This page is not available to you.')}</Blankslate.Description>
      </Blankslate>
    )
  }

  const data = overview.data
  const profiles = data?.profiles ?? []

  return (
    <Stack gap="spacious">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.LeadingVisual>
            <ContainerIcon />
          </PageHeader.LeadingVisual>
          <PageHeader.Title>VM profiles</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text>
            Each producer runs in a microVM of its own. A person gets their group&apos;s profile, an app its own; with
            none assigned, the default.
          </Text>
        </PageHeader.Description>
      </PageHeader>

      {feedback && (
        <Banner
          variant={feedback.variant}
          title={feedback.variant === 'success' ? 'Saved' : 'Not changed'}
          description={feedback.message}
          onDismiss={() => setFeedback(null)}
        />
      )}

      {data && (
        <Text size="small">
          Platform limits: at most {data.caps.max_memory_mib} MiB and {data.caps.max_vcpus} vCPUs, idle at most{' '}
          {data.caps.max_idle_before_pause_s === null ? 'without limit' : `${data.caps.max_idle_before_pause_s} s`}
          {data.caps.max_erase_after_idle_days != null &&
            `, an app's VM erased after at most ${data.caps.max_erase_after_idle_days} days unused`}
          .
          Awake-time quota:{' '}
          {data.quota.awake_vm_seconds_per_month === null
            ? 'unlimited'
            : `${data.quota.awake_vm_seconds_per_month} VM-seconds a month (not enforced yet)`}
          .
        </Text>
      )}

      {editing ? (
        <ProfileForm
          key={editing === 'new' ? 'new' : editing.id}
          initial={editing === 'new' ? BLANK : editing}
          onSave={(settings) => save.mutate(settings)}
          onCancel={() => setEditing(null)}
          saving={save.isPending}
          maxIdleDays={data?.caps.max_erase_after_idle_days ?? null}
        />
      ) : (
        <Stack direction="horizontal">
          <Button variant="primary" onClick={() => setEditing('new')} disabled={!data}>
            New profile
          </Button>
        </Stack>
      )}

      <Table.Container>
        <DataTable
          aria-label="VM profiles"
          data={profiles}
          columns={[
            {
              header: 'Profile',
              field: 'name',
              rowHeader: true,
              renderCell: (row) => (
                <Stack direction="horizontal" gap="condensed" align="center">
                  <Text weight="semibold">{row.name}</Text>
                  {row.is_default && <Label>Default</Label>}
                  {!row.within_caps && <Label variant="attention">Over the platform limits</Label>}
                </Stack>
              ),
            },
            {
              header: 'Memory',
              field: 'memory_mib',
              renderCell: (row) => `${row.normal_memory_mib} MiB, up to ${row.memory_mib} MiB`,
            },
            { header: 'vCPUs', field: 'vcpus' },
            { header: 'Idle before suspend', field: 'idle_before_pause_s', renderCell: (row) => idleLabel(row.idle_before_pause_s) },
            {
              header: "An app's unused VM",
              field: 'erase_after_idle_days',
              renderCell: (row) => idleErasureLabel(row.erase_after_idle_days),
            },
            {
              header: 'Packages',
              field: 'packages',
              renderCell: (row) =>
                [...row.packages.apt, ...row.packages.pip, ...row.packages.npm].join(', ') || '—',
            },
            {
              header: '',
              field: 'id',
              renderCell: (row) => (
                <Stack direction="horizontal" gap="condensed">
                  <Button size="small" onClick={() => setEditing(row)} aria-label={`Edit ${row.name}`}>
                    Edit
                  </Button>
                  {!row.is_default && (
                    <Button
                      size="small"
                      variant="danger"
                      onClick={() => remove.mutate(row.id)}
                      aria-label={`Delete ${row.name}`}
                    >
                      Delete
                    </Button>
                  )}
                </Stack>
              ),
            },
          ]}
        />
      </Table.Container>

      <Stack gap="normal">
        <Text weight="semibold">Groups</Text>
        <AssignmentTable
          label="Groups' VM profiles"
          rows={data?.groups ?? []}
          profiles={profiles}
          onAssign={(id, profileId) => assign.mutate({ kind: 'groups', id, profileId })}
        />
      </Stack>
      <Stack gap="normal">
        <Text weight="semibold">Apps</Text>
        <AssignmentTable
          label="Apps' VM profiles"
          rows={data?.apps ?? []}
          profiles={profiles}
          onAssign={(id, profileId) => assign.mutate({ kind: 'apps', id, profileId })}
        />
      </Stack>
    </Stack>
  )
}
