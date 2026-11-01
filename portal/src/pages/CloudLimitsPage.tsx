import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link as RouterLink } from 'react-router-dom'
import { Banner, Button, Checkbox, FormControl, Link, PageHeader, Stack, Text, TextInput } from '@primer/react'
import { Blankslate } from '@primer/react/experimental'
import { ContainerIcon } from '@primer/octicons-react'
import client from '../api/client'
import { refusalOf } from '../api/refusal'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import type { DeploymentOrg, OrgVmQuota, PlatformVmCaps, UserMe } from '../types'

type Saved = (message: string, ok: boolean) => void

function QuotaForm({ org, quota, onSaved }: { org: DeploymentOrg; quota: OrgVmQuota; onSaved: Saved }) {
  const queryClient = useQueryClient()
  const [value, setValue] = useState(
    quota.awake_vm_seconds_per_month === null ? '' : String(quota.awake_vm_seconds_per_month),
  )
  const save = useMutation({
    mutationFn: () =>
      client.put(`/portal/orgs/${org.id}/vm-quota`, {
        awake_vm_seconds_per_month: value.trim() === '' ? null : Number(value),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['vm-quota', org.id] })
      onSaved(`Quota for ${org.display_name} saved`, true)
    },
    onError: (error) => onSaved(refusalOf(error), false),
  })

  return (
    <Stack direction="horizontal" gap="normal" align="end" role="listitem">
      <FormControl>
        <FormControl.Label>{org.display_name}: awake VM-seconds a month</FormControl.Label>
        <TextInput type="number" min={0} value={value} onChange={(e) => setValue(e.target.value)} placeholder="Unlimited" />
      </FormControl>
      <Button onClick={() => save.mutate()} disabled={save.isPending}>
        Save
      </Button>
      <Link as={RouterLink} to={`/vm-profiles?org=${org.id}`}>
        VM profiles
      </Link>
    </Stack>
  )
}

function QuotaRow({ org, onSaved }: { org: DeploymentOrg; onSaved: Saved }) {
  const quota = useQuery({
    queryKey: ['vm-quota', org.id],
    queryFn: () => client.get<OrgVmQuota>(`/portal/orgs/${org.id}/vm-quota`).then((r) => r.data),
  })
  if (!quota.data) return null
  return <QuotaForm key={quota.data.updated_at ?? 'unset'} org={org} quota={quota.data} onSaved={onSaved} />
}

function CapsForm({ caps, onSaved }: { caps: PlatformVmCaps; onSaved: Saved }) {
  const queryClient = useQueryClient()
  const [memory, setMemory] = useState(String(caps.max_memory_mib))
  const [vcpus, setVcpus] = useState(String(caps.max_vcpus))
  const [idle, setIdle] = useState(String(caps.max_idle_before_pause_s ?? 3600))
  const [noIdleLimit, setNoIdleLimit] = useState(caps.max_idle_before_pause_s === null)
  const [eraseDays, setEraseDays] = useState(
    caps.max_erase_after_idle_days == null ? '' : String(caps.max_erase_after_idle_days),
  )
  const save = useMutation({
    mutationFn: () =>
      client.put('/portal/vm-caps', {
        max_memory_mib: Number(memory),
        max_vcpus: Number(vcpus),
        max_idle_before_pause_s: noIdleLimit ? null : Number(idle),
        // Not edited here: sent back as it is, so saving the others never resets it.
        ...(caps.max_volume_gib !== undefined && { max_volume_gib: caps.max_volume_gib }),
        max_erase_after_idle_days: eraseDays.trim() ? Number(eraseDays) : null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['vm-caps'] })
      onSaved('Platform limits saved. Profiles over them are served clamped.', true)
    },
    onError: (error) => onSaved(refusalOf(error), false),
  })

  return (
    <form
      aria-label="Platform limits"
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate()
      }}
    >
      <Stack gap="normal">
        <Stack direction="horizontal" gap="normal" wrap="wrap">
          <FormControl required>
            <FormControl.Label>Largest memory ceiling (MiB)</FormControl.Label>
            <TextInput type="number" min={128} value={memory} onChange={(e) => setMemory(e.target.value)} />
          </FormControl>
          <FormControl required>
            <FormControl.Label>Most vCPUs</FormControl.Label>
            <TextInput type="number" min={1} value={vcpus} onChange={(e) => setVcpus(e.target.value)} />
          </FormControl>
          <FormControl disabled={noIdleLimit}>
            <FormControl.Label>Longest idle before suspend (s)</FormControl.Label>
            <TextInput type="number" min={0} value={idle} onChange={(e) => setIdle(e.target.value)} />
          </FormControl>
        </Stack>
        <FormControl>
          <Checkbox checked={noIdleLimit} onChange={(e) => setNoIdleLimit(e.target.checked)} />
          <FormControl.Label>No limit on idle time (profiles may never suspend)</FormControl.Label>
        </FormControl>
        <FormControl>
          <FormControl.Label>Longest an app&apos;s VM may go unwoken before it is erased (days)</FormControl.Label>
          <TextInput
            type="number"
            min={1}
            max={3650}
            value={eraseDays}
            placeholder="No maximum"
            onChange={(e) => setEraseDays(e.target.value)}
          />
          <FormControl.Caption>
            Empty: no maximum, and a profile may keep an app&apos;s VM for ever. Set, every profile must erase an app&apos;s
            unwoken VM within it; profiles saved with longer (or never) are served with this many days. People&apos;s
            VMs are never erased this way.
          </FormControl.Caption>
        </FormControl>
        <Stack direction="horizontal">
          <Button type="submit" variant="primary" disabled={save.isPending}>
            Save limits
          </Button>
        </Stack>
      </Stack>
    </form>

  )
}

/**
 * The super admin's limits for the cloud: the caps every VM profile must
 * fit, and each organisation's quota of awake VM time — stored, not
 * enforced yet.
 */
export function CloudLimitsPage() {
  useDocumentTitle('Cloud limits')
  const [feedback, setFeedback] = useState<{ ok: boolean; message: string } | null>(null)
  const onSaved: Saved = (message, ok) => setFeedback({ ok, message })

  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  })
  const isSuperAdmin = me.data?.is_super_admin === true
  const caps = useQuery({
    queryKey: ['vm-caps'],
    queryFn: () => client.get<PlatformVmCaps>('/portal/vm-caps').then((r) => r.data),
    enabled: isSuperAdmin,
  })
  const orgs = useQuery({
    queryKey: ['deployment-organizations'],
    queryFn: () => client.get<DeploymentOrg[]>('/portal/deployment/organizations').then((r) => r.data),
    enabled: isSuperAdmin,
  })

  if (me.data && !isSuperAdmin) {
    return (
      <Blankslate>
        <Blankslate.Heading>Super admins only</Blankslate.Heading>
        <Blankslate.Description>The cloud&apos;s limits belong to the whole install.</Blankslate.Description>
      </Blankslate>
    )
  }

  return (
    <Stack gap="spacious">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.LeadingVisual>
            <ContainerIcon />
          </PageHeader.LeadingVisual>
          <PageHeader.Title>Cloud limits</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text>Every VM profile must fit these. Each organisation&apos;s awake-time quota is recorded, not enforced yet.</Text>
        </PageHeader.Description>
      </PageHeader>

      {feedback && (
        <Banner
          variant={feedback.ok ? 'success' : 'critical'}
          title={feedback.ok ? 'Saved' : 'Not saved'}
          description={feedback.message}
          onDismiss={() => setFeedback(null)}
        />
      )}

      {caps.data && <CapsForm key={caps.data.updated_at ?? 'defaults'} caps={caps.data} onSaved={onSaved} />}

      <Stack gap="normal" role="list" aria-label="Organisations' quotas">
        <Text weight="semibold">Quotas</Text>
        {(orgs.data ?? []).map((org) => (
          <QuotaRow key={org.id} org={org} onSaved={onSaved} />
        ))}
      </Stack>
    </Stack>
  )
}
