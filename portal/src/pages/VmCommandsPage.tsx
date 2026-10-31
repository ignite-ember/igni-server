import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Banner, Button, FormControl, Label, PageHeader, Select, Stack, Text, TextInput } from '@primer/react'
import { DataTable, Table } from '@primer/react/experimental'
import { TerminalIcon } from '@primer/octicons-react'
import client from '../api/client'
import { refusalOf } from '../api/refusal'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import type {
  JobScopeKind,
  OrgAppList,
  OrgGroupList,
  TeamMemberList,
  UserMe,
  VmCommandRecord,
  VmJobRecord,
  VmProfilesOverview,
} from '../types'

const ACTING_AS: Record<string, string> = {
  producer: 'in their own VM',
  org_admin: 'as org admin',
  super_admin: 'as super admin',
}

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString() : '—'
}

function StateLabel({ record }: { record: VmCommandRecord }) {
  if (record.state === 'finished') {
    const ok = record.exit_code === 0
    return <Label variant={ok ? 'success' : 'danger'}>{record.exit_code === null ? 'did not run' : `exit ${record.exit_code}`}</Label>
  }
  return <Label variant={record.state === 'cancelled' ? 'secondary' : 'attention'}>{record.state.replace('_', ' ')}</Label>
}

/**
 * Commands as root in producers' microVMs. A member runs them in their
 * own VM; an org admin in any VM of the organisation, and queues jobs
 * for many VMs that each run at that VM's next wake. Every command is in
 * the log below: who asked, in which VM, and what came of it.
 */
export function VmCommandsPage() {
  useDocumentTitle('Commands in VMs')
  const queryClient = useQueryClient()
  const [target, setTarget] = useState('')
  const [command, setCommand] = useState('')
  const [scopeKind, setScopeKind] = useState<JobScopeKind>('group')
  const [scopeId, setScopeId] = useState('')
  const [jobCommand, setJobCommand] = useState('')
  const [feedback, setFeedback] = useState<{ variant: 'success' | 'critical'; message: string } | null>(null)

  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  })
  const isAdmin = me.data?.role === 'admin'
  const isSuperAdmin = me.data?.is_super_admin === true
  const orgId = me.data?.org_id ?? ''

  const team = useQuery({
    queryKey: ['team'],
    queryFn: () => client.get<TeamMemberList>('/portal/team').then((r) => r.data),
    enabled: isAdmin,
  })
  const apps = useQuery({
    queryKey: ['apps'],
    queryFn: () => client.get<OrgAppList>('/portal/apps').then((r) => r.data),
    enabled: isAdmin,
  })
  const groups = useQuery({
    queryKey: ['groups-for-jobs'],
    queryFn: () => client.get<OrgGroupList>('/portal/groups').then((r) => r.data),
    enabled: isAdmin,
  })
  const profiles = useQuery({
    queryKey: ['vm-profiles', orgId],
    queryFn: () => client.get<VmProfilesOverview>(`/portal/orgs/${orgId}/vm-profiles`).then((r) => r.data),
    enabled: isAdmin && !!orgId,
  })
  const log = useQuery({
    queryKey: ['vm-commands'],
    queryFn: () => client.get<{ commands: VmCommandRecord[] }>('/vm/commands').then((r) => r.data.commands),
    enabled: !!me.data,
  })
  const jobs = useQuery({
    queryKey: ['vm-jobs'],
    queryFn: () => client.get<{ jobs: VmJobRecord[] }>('/vm/jobs').then((r) => r.data.jobs),
    enabled: isAdmin,
  })

  const refresh = (message: string) => {
    queryClient.invalidateQueries({ queryKey: ['vm-commands'] })
    queryClient.invalidateQueries({ queryKey: ['vm-jobs'] })
    setFeedback({ variant: 'success', message })
  }
  const failed = (error: unknown) => setFeedback({ variant: 'critical', message: refusalOf(error) })

  const run = useMutation({
    mutationFn: ({ kind, id }: { kind: string; id: string }) =>
      client.post('/vm/commands', { producer: { kind, id }, command }),
    onSuccess: () => {
      setCommand('')
      refresh('Queued. The VM is woken to run it.')
    },
    onError: failed,
  })
  const queue = useMutation({
    mutationFn: () =>
      client.post('/vm/jobs', {
        scope: { kind: scopeKind, id: scopeKind === 'group' || scopeKind === 'profile' ? scopeId : null },
        command: jobCommand,
      }),
    onSuccess: () => {
      setJobCommand('')
      refresh('Job queued. Each VM runs it at its next wake.')
    },
    onError: failed,
  })
  const cancelJob = useMutation({
    mutationFn: (jobId: string) => client.post(`/vm/jobs/${jobId}/cancel`),
    onSuccess: () => refresh('Job cancelled. VMs already handed it finish it.'),
    onError: failed,
  })

  const ownVm = me.data ? `person:${me.data.user_id}` : ''
  const chosen = target || ownVm
  const scopeChoices =
    scopeKind === 'group'
      ? (groups.data?.groups ?? []).map((g) => ({ id: g.id, name: g.name }))
      : scopeKind === 'profile'
        ? (profiles.data?.profiles ?? []).map((p) => ({ id: p.id, name: p.name }))
        : []

  return (
    <Stack gap="spacious">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.LeadingVisual>
            <TerminalIcon />
          </PageHeader.LeadingVisual>
          <PageHeader.Title>Commands in VMs</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text>
            Commands run as root in a producer&apos;s microVM. What a command installs lasts until an image upgrade;
            anything meant to last belongs in a VM profile.
          </Text>
        </PageHeader.Description>
      </PageHeader>

      {feedback && (
        <Banner
          variant={feedback.variant}
          title={feedback.variant === 'success' ? 'Done' : 'Not queued'}
          description={feedback.message}
          onDismiss={() => setFeedback(null)}
        />
      )}

      <form
        aria-label="Run a command"
        onSubmit={(e) => {
          e.preventDefault()
          const [kind, ...rest] = chosen.split(':')
          run.mutate({ kind, id: rest.join(':') })
        }}
      >
        <Stack gap="normal">
          <Text weight="semibold">Run a command in one VM</Text>
          <Stack direction="horizontal" gap="normal" wrap="wrap" align="end">
            <FormControl>
              <FormControl.Label>VM</FormControl.Label>
              <Select value={chosen} onChange={(e) => setTarget(e.target.value)}>
                <Select.Option value={ownVm}>My own VM</Select.Option>
                {isAdmin &&
                  (team.data?.members ?? [])
                    .filter((m) => m.status === 'active' && m.user_id !== me.data?.user_id)
                    .map((m) => (
                      <Select.Option key={m.user_id} value={`person:${m.user_id}`}>
                        {m.full_name || m.email}
                      </Select.Option>
                    ))}
                {isAdmin &&
                  (apps.data?.apps ?? []).map((a) => (
                    <Select.Option key={a.app_id} value={`app:${a.app_id}`}>
                      {`App: ${a.name}`}
                    </Select.Option>
                  ))}
              </Select>
            </FormControl>
            <FormControl required>
              <FormControl.Label>Command</FormControl.Label>
              <TextInput value={command} onChange={(e) => setCommand(e.target.value)} placeholder="apt-get install -y jq" />
            </FormControl>
            <Button type="submit" variant="primary" disabled={!command.trim() || !chosen || run.isPending}>
              Run
            </Button>
          </Stack>
        </Stack>
      </form>

      {isAdmin && (
        <form
          aria-label="Queue a job"
          onSubmit={(e) => {
            e.preventDefault()
            queue.mutate()
          }}
        >
          <Stack gap="normal">
            <Text weight="semibold">Queue a job for many VMs</Text>
            <Text size="small">Nobody is woken: each VM in scope runs it at its next wake.</Text>
            <Stack direction="horizontal" gap="normal" wrap="wrap" align="end">
              <FormControl>
                <FormControl.Label>For</FormControl.Label>
                <Select
                  value={scopeKind}
                  onChange={(e) => {
                    setScopeKind(e.target.value as JobScopeKind)
                    setScopeId('')
                  }}
                >
                  <Select.Option value="group">A group</Select.Option>
                  <Select.Option value="profile">A VM profile</Select.Option>
                  <Select.Option value="org">The whole organisation</Select.Option>
                  {isSuperAdmin && <Select.Option value="platform">Every organisation</Select.Option>}
                </Select>
              </FormControl>
              {(scopeKind === 'group' || scopeKind === 'profile') && (
                <FormControl required>
                  <FormControl.Label>{scopeKind === 'group' ? 'Group' : 'Profile'}</FormControl.Label>
                  <Select value={scopeId} onChange={(e) => setScopeId(e.target.value)}>
                    <Select.Option value="">Choose…</Select.Option>
                    {scopeChoices.map((c) => (
                      <Select.Option key={c.id} value={c.id}>
                        {c.name}
                      </Select.Option>
                    ))}
                  </Select>
                </FormControl>
              )}
              <FormControl required>
                <FormControl.Label>Command</FormControl.Label>
                <TextInput value={jobCommand} onChange={(e) => setJobCommand(e.target.value)} />
              </FormControl>
              <Button
                type="submit"
                disabled={
                  !jobCommand.trim() || ((scopeKind === 'group' || scopeKind === 'profile') && !scopeId) || queue.isPending
                }
              >
                Queue job
              </Button>
            </Stack>
          </Stack>
        </form>
      )}

      {isAdmin && (jobs.data ?? []).length > 0 && (
        <Table.Container>
          <Table.Title id="vm-jobs">
            Jobs
          </Table.Title>
          <DataTable
            aria-labelledby="vm-jobs"
            data={(jobs.data ?? []).map((job) => ({ id: job.job_id, ...job }))}
            columns={[
              { header: 'Command', field: 'command', rowHeader: true, renderCell: (row) => <code>{row.command}</code> },
              { header: 'For', field: 'scope', renderCell: (row) => row.scope.kind },
              { header: 'Handed out', field: 'handed_out' },
              { header: 'Finished', field: 'finished' },
              { header: 'Queued', field: 'created_at', renderCell: (row) => when(row.created_at) },
              {
                header: '',
                field: 'cancelled_at',
                renderCell: (row) =>
                  row.cancelled_at ? (
                    <Label variant="secondary">cancelled</Label>
                  ) : (
                    <Button size="small" onClick={() => cancelJob.mutate(row.job_id)} aria-label={`Cancel ${row.command}`}>
                      Cancel
                    </Button>
                  ),
              },
            ]}
          />
        </Table.Container>
      )}

      <Table.Container>
        <Table.Title id="vm-command-log">
          Audit log
        </Table.Title>
        <DataTable
          aria-labelledby="vm-command-log"
          data={(log.data ?? []).map((record) => ({ id: record.command_id, ...record }))}
          columns={[
            { header: 'Command', field: 'command', rowHeader: true, renderCell: (row) => <code>{row.command}</code> },
            { header: 'VM', field: 'producer', renderCell: (row) => `${row.producer.kind} ${row.producer.id}` },
            {
              header: 'Asked by',
              field: 'requested_by',
              renderCell: (row) => `${row.requested_by.id} ${ACTING_AS[row.requested_by.acting_as] ?? ''}`,
            },
            { header: 'Started', field: 'started_at', renderCell: (row) => when(row.started_at) },
            { header: 'Ended', field: 'finished_at', renderCell: (row) => when(row.finished_at) },
            { header: 'Result', field: 'state', renderCell: (row) => <StateLabel record={row} /> },
          ]}
        />
      </Table.Container>
    </Stack>
  )
}
