/**
 * Inference — the customer's own GPU fleet, live, and how they manage it.
 *
 * Every node is probed whatever its source, and one that cannot be
 * reached stays listed and turns red: "it is down" beats "it is gone".
 * Admin-only, because base URLs describe a private network.
 */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { isAxiosError } from 'axios'
import { Banner, Button, PageHeader, Stack, Text } from '@primer/react'
import { Blankslate, SkeletonText } from '@primer/react/experimental'
import { CpuIcon, PlusIcon } from '@primer/octicons-react'

import client from '../api/client'
import { FleetList } from '../components/inference/FleetList'
import { FleetSummary } from '../components/inference/FleetSummary'
import { INFERENCE_STATUS_KEY } from '../components/inference/fleet'
import { NodeDialog } from '../components/inference/NodeDialog'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import { useMe } from '../hooks/useMe'
import type { InferenceStatus } from '../types'
import './InferencePage.css'

/** In-flight counts change constantly, and this page is about "how busy right now". */
const REFRESH_MS = 10_000

export function InferencePage() {
  useDocumentTitle('Inference')
  const [adding, setAdding] = useState(false)

  const statusQuery = useQuery({
    queryKey: INFERENCE_STATUS_KEY,
    queryFn: () => client.get<InferenceStatus>('/portal/inference').then((r) => r.data),
    refetchInterval: REFRESH_MS,
  })
  const forbidden = isAxiosError(statusQuery.error) && statusQuery.error.response?.status === 403

  // The fleet serves every organisation, so changing it is a super admin's (SER-32).
  const meQuery = useMe()
  const canManage = meQuery.data?.is_super_admin === true

  return (
    <Stack gap="normal">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.Title as="h1">Inference</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text size="small" className="inference-muted">
            The GPU nodes this deployment routes to, how busy they are, and whether they are answering.
            {!forbidden && meQuery.data && !canManage && (
              <> Only a super admin can add or change nodes: they serve every organisation.</>
            )}
          </Text>
        </PageHeader.Description>
        {!forbidden && canManage && (
          <PageHeader.Actions>
            <Button variant="primary" leadingVisual={PlusIcon} onClick={() => setAdding(true)}>
              Add node
            </Button>
          </PageHeader.Actions>
        )}
      </PageHeader>

      {forbidden && <AdminsOnly />}

      {statusQuery.isError && !forbidden && (
        <Banner variant="critical" title="Could not load inference status">
          {statusQuery.error.message}
        </Banner>
      )}

      {statusQuery.isPending && <SkeletonText lines={4} />}

      {statusQuery.data && (
        <Stack gap="spacious">
          <FleetSummary status={statusQuery.data} />
          <FleetList status={statusQuery.data} canManage={canManage} />
          <RegistrationNote status={statusQuery.data} />
        </Stack>
      )}

      {adding && <NodeDialog onClose={() => setAdding(false)} />}
    </Stack>
  )
}

function AdminsOnly() {
  return (
    <Blankslate spacious>
      <Blankslate.Visual>
        <CpuIcon size={24} />
      </Blankslate.Visual>
      <Blankslate.Heading>Admins only</Blankslate.Heading>
      <Blankslate.Description>
        Inference node statistics describe the internal network your GPUs sit on. Ask an organization admin if you
        need them.
      </Blankslate.Description>
    </Blankslate>
  )
}

/** "Nothing has registered" and "registration is off" look identical in the list, and need different fixes. */
function RegistrationNote({ status }: { status: InferenceStatus }) {
  return (
    <Text size="small" className="inference-muted">
      {status.registration_enabled
        ? `Nodes may also register themselves, and are expected to check in every ${status.heartbeat_interval_seconds}s. One that stops leaves rotation on its own.`
        : 'Nodes cannot register themselves on this deployment. Set node_registration_token to let a GPU cluster announce itself when it starts.'}
    </Text>
  )
}
