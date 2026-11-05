import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Label, Stack, Text, TextInput } from '@primer/react'
import client from '../api/client'
import { refusalOf } from '../api/refusal'
import type { VmProfileRequest } from '../types'
import './VmProfileRequests.css'

const STATE_LABEL = {
  pending: { text: 'Pending', variant: 'attention' },
  approved: { text: 'Approved', variant: 'success' },
  rejected: { text: 'Rejected', variant: 'secondary' },
} as const

function subjectLabel(request: VmProfileRequest): string {
  return `${request.subject.kind === 'app' ? 'App' : 'Extension'} ${request.subject.name}`
}

/** Exactly what would run as root, and the size asked for. */
function WhatWouldRun({ request }: { request: VmProfileRequest }) {
  const r = request.requested
  const managers = (['apt', 'pip', 'npm'] as const).filter((m) => r.packages[m].length > 0)
  return (
    <Stack gap="condensed">
      {r.internet ? (
        <Text size="small" weight="semibold" className="vm-request-internet" role="note">
          Asks for internet access: approving lets its VM reach the internet, not only what its node allows.
        </Text>
      ) : (
        <Text size="small">Internal network only.</Text>
      )}
      <Text size="small">
        {r.normal_memory_mib} MiB, up to {r.memory_mib} MiB · {r.vcpus} vCPU
        {r.vcpus === 1 ? '' : 's'} · idle before suspend{' '}
        {r.idle_before_pause_s === null ? 'never' : `${r.idle_before_pause_s} s`}
      </Text>
      {managers.length === 0 ? (
        <Text size="small">No packages.</Text>
      ) : (
        managers.map((m) => (
          <Text key={m} size="small">
            <Text weight="semibold">{m}:</Text> {r.packages[m].join(', ')}
          </Text>
        ))
      )}
      {r.setup_script ? (
        <Stack gap="condensed">
          <Text size="small" weight="semibold">
            Setup script — runs as root when the layer is built
          </Text>
          <pre aria-label={`Setup script of ${subjectLabel(request)}`}>{r.setup_script}</pre>
        </Stack>
      ) : (
        <Text size="small">No setup script.</Text>
      )}
    </Stack>
  )
}

function RequestCard({
  request,
  onApprove,
  onReject,
  busy,
}: {
  request: VmProfileRequest
  onApprove: () => void
  onReject: (reason: string | null) => void
  busy: boolean
}) {
  const [reason, setReason] = useState('')
  const label = subjectLabel(request)
  const state = STATE_LABEL[request.state]
  return (
    <section className="vm-request" aria-label={`Request of ${label}`}>
      <Stack gap="normal">
        <Stack direction="horizontal" gap="condensed" align="center" wrap="wrap">
          <Text weight="semibold">{label}</Text>
          <Label variant={state.variant}>{state.text}</Label>
          {!request.within_caps && <Label variant="danger">Over the platform limits</Label>}
          {request.requested.internet && <Label variant="danger">Internet access</Label>}
        </Stack>
        <WhatWouldRun request={request} />
        {request.state === 'rejected' && request.reason && <Text size="small">Reason: {request.reason}</Text>}
        {request.state !== 'approved' && (
          <Stack direction="horizontal" gap="condensed" align="end" wrap="wrap">
            <Button
              variant="primary"
              size="small"
              onClick={onApprove}
              disabled={busy || !request.within_caps}
              aria-label={request.requested.internet ? `Approve ${label}, with internet access` : `Approve ${label}`}
            >
              {request.requested.internet ? 'Approve, with internet' : 'Approve'}
            </Button>
            {request.state === 'pending' && (
              <>
                <TextInput
                  size="small"
                  aria-label={`Reason for rejecting ${label}`}
                  placeholder="Reason (optional)"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  maxLength={500}
                />
                <Button
                  variant="danger"
                  size="small"
                  onClick={() => onReject(reason.trim() || null)}
                  disabled={busy}
                  aria-label={`Reject ${label}`}
                >
                  Reject
                </Button>
              </>
            )}
          </Stack>
        )}
      </Stack>
    </section>
  )
}

/**
 * What apps and extensions ask their VMs to get. Nothing in a request runs
 * until an admin approves it; approving names the version read, so a request
 * that changed meanwhile is refused rather than approved unseen.
 */
export function VmProfileRequests({
  orgId,
  onDone,
  onFailed,
}: {
  orgId: string
  onDone: (message: string) => void
  onFailed: (error: unknown) => void
}) {
  const queryClient = useQueryClient()
  const requests = useQuery({
    queryKey: ['vm-profile-requests', orgId],
    queryFn: () =>
      client
        .get<{ requests: VmProfileRequest[] }>(`/portal/orgs/${orgId}/vm-profile-requests`)
        .then((r) => r.data.requests),
    enabled: !!orgId,
  })
  const settled = (message: string) => {
    queryClient.invalidateQueries({ queryKey: ['vm-profile-requests', orgId] })
    queryClient.invalidateQueries({ queryKey: ['vm-profiles', orgId] })
    onDone(message)
  }
  const failed = (error: unknown) => {
    queryClient.invalidateQueries({ queryKey: ['vm-profile-requests', orgId] })
    onFailed(error)
  }
  const approve = useMutation({
    mutationFn: (request: VmProfileRequest) =>
      client.post(`/portal/orgs/${orgId}/vm-profile-requests/${request.id}/approve`, { version: request.version }),
    onSuccess: (_, request) =>
      settled(
        request.subject.kind === 'app'
          ? `Approved: ${request.subject.name} has its profile`
          : `Approved: give the new profile to the groups and apps that use ${request.subject.name}`,
      ),
    onError: failed,
  })
  const reject = useMutation({
    mutationFn: ({ request, reason }: { request: VmProfileRequest; reason: string | null }) =>
      client.post(`/portal/orgs/${orgId}/vm-profile-requests/${request.id}/reject`, { reason }),
    onSuccess: () => settled('Rejected; the VM keeps the profile it has'),
    onError: failed,
  })

  const all = requests.data ?? []
  const open = all.filter((r) => r.state !== 'approved')
  const approved = all.filter((r) => r.state === 'approved')
  const busy = approve.isPending || reject.isPending

  return (
    <Stack gap="normal">
      <Text weight="semibold">Requests</Text>
      <Text size="small">
        Apps and extensions ask for packages, a setup script that runs as root, more memory or CPUs, and internet
        access. Nothing they ask for runs until you approve it.
      </Text>
      {requests.isError && <Text size="small">{refusalOf(requests.error)}</Text>}
      {requests.isSuccess && all.length === 0 && <Text size="small">No app or extension has asked for anything.</Text>}
      {open.map((request) => (
        <RequestCard
          key={request.id}
          request={request}
          busy={busy}
          onApprove={() => approve.mutate(request)}
          onReject={(reason) => reject.mutate({ request, reason })}
        />
      ))}
      {approved.length > 0 && <Text size="small">Approved: {approved.map((r) => subjectLabel(r)).join(', ')}.</Text>}
    </Stack>
  )
}
