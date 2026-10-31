import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Banner, Button, Heading, Label, RelativeTime, Stack, Text } from '@primer/react'
import client from '../api/client'
import { refusalOf } from '../api/refusal'
import type { HeldExtensionUpdate, RegisteredClient } from '../types'

export type HeldUpdateDecision = 'approve' | 'reject'

/**
 * A newer version of an approved service's extension that asks for more
 * than the version the super admin approved: a host, a path, a secret, a
 * tool, a hook, a prompt section.
 *
 * igni server holds it: machines keep receiving the approved version
 * until somebody decides here. Approving makes it the version every
 * machine receives; rejecting discards it, and the service stays on the
 * approved one until it uploads another. Each decision is recorded in
 * the audit trail with what was added.
 */
export function HeldUpdatePanel({ held }: { held: HeldExtensionUpdate }) {
  const queryClient = useQueryClient()
  const decide = useMutation({
    mutationFn: (decision: HeldUpdateDecision) => {
      // Each path written out whole, so the route check can match it.
      const id = encodeURIComponent(held.client_id)
      if (decision === 'approve') return client.post<RegisteredClient>(`/portal/clients/${id}/held-update/approve`)
      return client.post<RegisteredClient>(`/portal/clients/${id}/held-update/reject`)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['registered-clients'] })
      void queryClient.invalidateQueries({ queryKey: ['registered-client', held.client_id] })
    },
  })
  const added = held.comparison.added
  const approved = held.approved_version ?? 'the approved version'

  return (
    <section aria-labelledby="held-update" className="settings-card service-section services-card-pending">
      <Stack gap="normal">
        <Stack gap="condensed">
          <Stack direction="horizontal" align="center" gap="condensed" wrap="wrap">
            <Heading as="h2" variant="small" id="held-update">
              Update asks for more
            </Heading>
            <Label variant="attention">Version {held.version} held</Label>
          </Stack>
          <Text size="small">
            {held.service} uploaded version {held.version} of {held.name}{' '}
            <RelativeTime datetime={held.uploaded_at} />. It asks for more than version {approved}, the one you
            approved, so people&apos;s machines keep receiving version {approved} until you decide.
          </Text>
        </Stack>

        <Stack gap="condensed">
          <Text as="h3" size="medium" weight="semibold" className="service-part-heading">
            What it adds
          </Text>
          <ul className="service-list" aria-label="What it adds">
            {added.map((grant) => (
              <li key={`${grant.kind}:${grant.item}`}>
                <Text weight="semibold">{grant.label}:</Text> <code className="service-code">{grant.item}</code>
                {grant.was !== null && <Text className="settings-section-caption"> (was {grant.was})</Text>}
              </li>
            ))}
          </ul>
          <Text size="small" className="settings-section-caption">
            Everything else it asks for, version {approved} already had. The rest of this page describes version{' '}
            {approved}.
          </Text>
        </Stack>

        {decide.isError && <Banner variant="critical" title="Not changed" description={refusalOf(decide.error)} />}

        <Stack direction="horizontal" gap="condensed" wrap="wrap">
          <Button
            variant="primary"
            disabled={decide.isPending}
            onClick={() => decide.mutate('approve')}
            aria-label={`Approve version ${held.version} of ${held.name}`}
          >
            Approve
          </Button>
          <Button
            variant="danger"
            disabled={decide.isPending}
            onClick={() => decide.mutate('reject')}
            aria-label={`Reject version ${held.version} of ${held.name}`}
          >
            Reject
          </Button>
        </Stack>
        <Text size="small" className="settings-section-caption">
          Approve, and every machine it is on for receives version {held.version}. Reject, and it is discarded:{' '}
          {held.service} stays on version {approved} until it uploads another.
        </Text>
      </Stack>
    </section>
  )
}
