import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link as RouterLink } from 'react-router-dom'
import {
  Banner,
  Button,
  Label,
  Link,
  PageHeader,
  RelativeTime,
  Stack,
  Text,
} from '@primer/react'
import { Blankslate } from '@primer/react/experimental'
import { ArrowDownIcon, ArrowUpIcon, PackageIcon, ServerIcon } from '@primer/octicons-react'
import client from '../api/client'
import { refusalOf } from '../api/refusal'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import { useServiceDecision, type ServiceDecision } from '../hooks/useServiceDecision'
import { STATE_LABEL, STATE_ORDER } from '../lib/registeredClients'
import type { ExtensionPriority, RegisteredClient, UserMe } from '../types'
import './cards.css'
import './ServicesPage.css'

/** Pending first; newest first within each state. */
function inOrder(clients: RegisteredClient[]): RegisteredClient[] {
  return [...clients].sort(
    (a, b) => STATE_ORDER[a.state] - STATE_ORDER[b.state] || b.created_at.localeCompare(a.created_at),
  )
}

function ClientCard({
  registration,
  busy,
  onDecide,
}: {
  registration: RegisteredClient
  busy: boolean
  onDecide: (decision: ServiceDecision) => void
}) {
  const state = STATE_LABEL[registration.state]
  const pending = registration.state === 'pending'

  return (
    <Stack
      gap="normal"
      padding="normal"
      role="listitem"
      aria-label={registration.name}
      className={`settings-card services-card${pending ? ' services-card-pending' : ''}`}
    >
      <Stack direction="horizontal" align="center" justify="space-between" wrap="wrap" gap="condensed">
        <Stack direction="horizontal" align="center" gap="condensed" wrap="wrap">
          <Text weight="semibold">{registration.name}</Text>
          <Label variant="secondary">{registration.kind === 'service' ? 'Service' : 'App'}</Label>
          <Label variant={state.variant}>{state.text}</Label>
          {registration.held_version && (
            <Label variant="attention">Update asks for more: {registration.held_version}</Label>
          )}
        </Stack>
        <Stack direction="horizontal" gap="condensed">
          {registration.state !== 'approved' && (
            <Button
              variant={pending ? 'primary' : 'default'}
              onClick={() => onDecide('approve')}
              disabled={busy}
              aria-label={`Approve ${registration.name}`}
            >
              Approve
            </Button>
          )}
          {registration.state === 'approved' && (
            <Button
              variant="danger"
              onClick={() => onDecide('revoke')}
              disabled={busy}
              aria-label={`Revoke ${registration.name}`}
            >
              Revoke
            </Button>
          )}
          {registration.state !== 'approved' && (
            <Button
              variant="danger"
              onClick={() => onDecide('remove')}
              disabled={busy}
              aria-label={`Remove ${registration.name}`}
            >
              Remove
            </Button>
          )}
        </Stack>
      </Stack>

      <ExtensionSummary registration={registration} />

      <Stack gap="condensed">
        <Text size="small" weight="semibold" id={`thumbprint-${registration.client_id}`}>
          Key thumbprint
        </Text>
        <code className="services-thumbprint" aria-labelledby={`thumbprint-${registration.client_id}`}>
          {registration.thumbprint}
        </code>
        {pending && (
          <Text size="small" className="settings-section-caption">
            Approve only if this matches, character for character, the thumbprint {registration.name} printed in
            its own log when it started.
          </Text>
        )}
      </Stack>

      <Stack gap="none">
        <Text size="small">
          Web:{' '}
          <Link className="services-detail" href={registration.base_url} target="_blank" rel="noopener noreferrer">
            {registration.base_url}
          </Link>
        </Text>
        {registration.redirect_uris.length > 0 && (
          <Text size="small">
            Returns people to:{' '}
            <span className="services-detail">{registration.redirect_uris.join(', ')}</span>
          </Text>
        )}
        <Text size="small">
          Client ID: <span className="services-detail">{registration.client_id}</span>
        </Text>
        <Text size="small" className="settings-section-caption">
          Registered <RelativeTime datetime={registration.created_at} />
          {registration.decided_at && (
            <>
              {' '}
              · {registration.state === 'revoked' ? 'revoked' : 'approved'}{' '}
              <RelativeTime datetime={registration.decided_at} />
            </>
          )}
        </Text>
      </Stack>
    </Stack>
  )
}

/**
 * What the service ships to people's machines, in a line or two, and the
 * way to all of it. Approving is approving this as much as the key, so
 * it sits above the thumbprint rather than below the addresses.
 */
function ExtensionSummary({ registration }: { registration: RegisteredClient }) {
  const extension = registration.extension
  const opens = extension ? 'See the whole extension' : 'Details'
  return (
    <Stack direction="horizontal" align="start" justify="space-between" wrap="wrap" gap="condensed">
      <Stack direction="horizontal" align="start" gap="condensed" className="services-extension">
        <span className="services-extension-icon">
          <PackageIcon />
        </span>
        {extension ? (
          <Stack gap="none">
            <Stack direction="horizontal" align="center" gap="condensed" wrap="wrap">
              <Text size="small" weight="semibold">
                Extension: {extension.name}
              </Text>
              <Label variant="secondary">{extension.version}</Label>
            </Stack>
            {extension.description && (
              <Text size="small" className="settings-section-caption">
                {extension.description}
              </Text>
            )}
          </Stack>
        ) : (
          <Text size="small" className="settings-section-caption">
            Has not uploaded its extension yet.
          </Text>
        )}
      </Stack>
      <Button
        as={RouterLink}
        to={`/services/${encodeURIComponent(registration.client_id)}`}
        size="small"
        aria-label={`${opens}: ${registration.name}`}
      >
        {opens}
      </Button>
    </Stack>
  )
}

/**
 * Services and apps that registered themselves with this install.
 *
 * A service registers on start with nothing but its public key, and is
 * given nothing until a super admin approves it here. The thumbprint is
 * how they know it is theirs: the service prints it in its own log, and
 * the two must match before approving — registration is unauthenticated,
 * so anybody who can reach igni can ask.
 */
/**
 * The priority order of extensions, highest first.
 *
 * When two extensions want one thing — the same prompt, the same section,
 * the same slash command — the higher one has it, on every machine, with
 * nothing to decide case by case and nothing held back. Edits stack and
 * extensions' hooks are asked in the same order. Unset, it is the order the
 * services were approved in. Saving sends the whole order; every machine
 * receives it with the extensions.
 */
function Priority() {
  const queryClient = useQueryClient()
  const order = useQuery({
    queryKey: ['extension-priority'],
    queryFn: () => client.get<ExtensionPriority>('/portal/extension-priority').then((r) => r.data),
  })
  const [draft, setDraft] = useState<string[] | null>(null)
  const save = useMutation({
    mutationFn: (ids: string[]) =>
      client.put<ExtensionPriority>('/portal/extension-priority', { order: ids }).then((r) => r.data),
    onSuccess: (saved) => {
      queryClient.setQueryData(['extension-priority'], saved)
      setDraft(null)
    },
  })
  const entries = order.data?.entries ?? []
  if (entries.length < 2) return null
  const names = new Map(entries.map((entry) => [entry.extension_id, entry]))
  const ids = draft ?? entries.map((entry) => entry.extension_id)
  const move = (index: number, by: -1 | 1) => {
    const next = [...ids]
    const [moved] = next.splice(index, 1)
    next.splice(index + by, 0, moved)
    setDraft(next)
  }

  return (
    <Stack gap="condensed" padding="normal" className="settings-card" aria-label="Priority of extensions">
      <Stack gap="none">
        <Text weight="semibold">Priority, highest first</Text>
        <Text size="small" className="settings-section-caption">
          When two extensions want the same thing — a prompt, a section of one, a slash command — the higher one has
          it, on every machine. Several extensions&apos; prompt edits stack in this order, and igni asks their
          hooks in it, each handed what the one before left.{' '}
          {order.data?.set_by ? (
            <>
              Set by {order.data.set_by}
              {order.data.set_at && (
                <>
                  {' '}
                  <RelativeTime datetime={order.data.set_at} />
                </>
              )}
              .
            </>
          ) : (
            'Nobody has set it, so it is the order their services were approved in.'
          )}
        </Text>
      </Stack>
      {save.isError && <Banner variant="critical" title="Not saved" description={refusalOf(save.error)} />}
      <ol className="services-priority">
        {ids.map((id, index) => (
          <li key={id}>
            <Stack direction="horizontal" align="center" justify="space-between" gap="condensed">
              <Text>
                {names.get(id)?.name ?? id} <Text size="small" className="settings-section-caption">from {names.get(id)?.service}</Text>
              </Text>
              <Stack direction="horizontal" gap="condensed">
                <Button
                  size="small"
                  leadingVisual={ArrowUpIcon}
                  disabled={index === 0 || save.isPending}
                  onClick={() => move(index, -1)}
                  aria-label={`Move ${names.get(id)?.name ?? id} higher`}
                >
                  Higher
                </Button>
                <Button
                  size="small"
                  leadingVisual={ArrowDownIcon}
                  disabled={index === ids.length - 1 || save.isPending}
                  onClick={() => move(index, 1)}
                  aria-label={`Move ${names.get(id)?.name ?? id} lower`}
                >
                  Lower
                </Button>
              </Stack>
            </Stack>
          </li>
        ))}
      </ol>
      {draft && (
        <Stack direction="horizontal" gap="condensed">
          <Button variant="primary" disabled={save.isPending} onClick={() => save.mutate(draft)}>
            Save this order
          </Button>
          <Button disabled={save.isPending} onClick={() => setDraft(null)}>
            Discard
          </Button>
        </Stack>
      )}
    </Stack>
  )
}

export function ServicesPage() {
  useDocumentTitle('Services')
  const { decide, request, dialog } = useServiceDecision()

  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  })
  const clients = useQuery({
    queryKey: ['registered-clients'],
    queryFn: () => client.get<RegisteredClient[]>('/portal/clients').then((r) => r.data),
    enabled: me.data?.is_super_admin === true,
  })

  if (me.data && me.data.is_super_admin !== true) {
    return (
      <Blankslate>
        <Blankslate.Heading>Super admins only</Blankslate.Heading>
        <Blankslate.Description>This page is for the people who own this igni install.</Blankslate.Description>
      </Blankslate>
    )
  }

  const registrations = inOrder(clients.data ?? [])
  const waiting = registrations.filter((r) => r.state === 'pending').length
  const held = registrations.filter((r) => r.held_version).length

  return (
    <Stack gap="spacious">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.LeadingVisual>
            <ServerIcon />
          </PageHeader.LeadingVisual>
          <PageHeader.Title>Services</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text>
            Services such as CodeIndex register themselves with this install when they start. An approved one can
            get tokens for every organisation here, and its extension goes to every signed-in machine, so compare its
            key thumbprint with the one it printed in its own log, and read what its extension does, before approving.
            Anybody who can reach igni can register; only the thumbprint says it is yours.
          </Text>
        </PageHeader.Description>
      </PageHeader>

      {waiting > 0 && (
        <Banner
          variant="warning"
          title={waiting === 1 ? '1 service is waiting for approval' : `${waiting} services are waiting for approval`}
          description="Check each thumbprint against the service's log. If it does not match, remove the registration."
        />
      )}

      {held > 0 && (
        <Banner
          variant="warning"
          title={
            held === 1
              ? "1 service's update asks for more than you approved"
              : `${held} services' updates ask for more than you approved`
          }
          description="Machines keep receiving the approved version. Open the service to see what the update adds, then approve or reject it."
        />
      )}

      {decide.isError && <Banner variant="critical" title="Not changed" description={refusalOf(decide.error)} />}
      {clients.isError && (
        <Banner variant="critical" title="Could not load services" description={refusalOf(clients.error)} />
      )}

      {clients.isSuccess && registrations.length === 0 ? (
        <Blankslate>
          <Blankslate.Heading>No services have registered</Blankslate.Heading>
          <Blankslate.Description>
            A service registers when it starts with this install&apos;s address, and waits here for approval.
          </Blankslate.Description>
        </Blankslate>
      ) : (
        <Stack gap="normal" role="list" aria-label="Services">
          {registrations.map((registration) => (
            <ClientCard
              key={registration.client_id}
              registration={registration}
              busy={decide.isPending}
              onDecide={(decision) => request(registration, decision)}
            />
          ))}
        </Stack>
      )}

      {clients.isSuccess && <Priority />}

      {dialog}
    </Stack>
  )
}
