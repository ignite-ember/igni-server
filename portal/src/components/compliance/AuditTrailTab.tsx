import { useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Banner, Button, FormControl, Label, Select, Spinner, Stack, Text, TextInput } from '@primer/react'
import { DownloadIcon } from '@primer/octicons-react'

import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import type { AuditEvent, AuditEventPage, AuditOutcome } from '../../types'

const OUTCOME_COLOUR: Record<AuditOutcome, 'success' | 'attention' | 'danger'> = {
  success: 'success',
  denied: 'attention',
  error: 'danger',
}

/** Every privileged action, newest first, filterable by outcome and target. */
export function AuditTrailTab() {
  const [outcome, setOutcome] = useState<AuditOutcome | ''>('')
  const [targetId, setTargetId] = useState('')
  // Keyset pagination: the cursor is a timestamp, and the server hands out
  // only the next one, so a stack of them is what makes Newer work.
  const [cursors, setCursors] = useState<string[]>([])

  const before = cursors[cursors.length - 1]

  const query = useQuery({
    queryKey: ['audit-events', outcome, targetId, before],
    placeholderData: keepPreviousData,
    queryFn: () => {
      const params = new URLSearchParams({ limit: '50' })
      if (outcome) params.set('outcome', outcome)
      if (targetId) params.set('target_id', targetId)
      if (before) params.set('before', before)
      return client.get<AuditEventPage>(`/portal/audit-events?${params}`).then((r) => r.data)
    },
  })

  // A `before` from one filtered view means nothing in another, so any
  // filter change starts again from the newest page.
  const resetTo = (change: () => void) => {
    setCursors([])
    change()
  }

  return (
    <Stack gap="normal">
      <Text size="small" className="compliance-hint">
        Every privileged action, newest first. <strong>Denied</strong> is the one worth
        reading weekly. Route templates rather than paths, so an identifier appears in
        the target column and not in the route.
      </Text>

      <Stack direction="horizontal" gap="normal" wrap="wrap" align="end">
        <FormControl>
          <FormControl.Label>Outcome</FormControl.Label>
          {/* Primer's `Select`: `FormControl` labels only children it recognises. */}
          <Select
            value={outcome}
            onChange={(e) => resetTo(() => setOutcome(e.target.value as AuditOutcome | ''))}
          >
            <Select.Option value="">Any</Select.Option>
            <Select.Option value="success">Success</Select.Option>
            <Select.Option value="denied">Denied</Select.Option>
            <Select.Option value="error">Error</Select.Option>
          </Select>
        </FormControl>

        <FormControl>
          <FormControl.Label>Target id</FormControl.Label>
          <TextInput
            value={targetId}
            placeholder="Anything acted on"
            onChange={(e) => resetTo(() => setTargetId(e.target.value))}
          />
        </FormControl>

        <Button
          leadingVisual={DownloadIcon}
          onClick={() => {
            // A plain navigation so the browser streams the (possibly
            // 50 000-line) export to disk instead of buffering a blob.
            window.location.href = '/v1/portal/audit-events/export'
          }}
        >
          Export as JSON Lines
        </Button>
      </Stack>

      {query.isError && (
        <Banner
          variant="critical"
          title="Could not read the audit trail"
          description={refusalOf(query.error)}
        />
      )}

      {query.isLoading ? (
        <Spinner />
      ) : (
        <>
          <div className="compliance-table-scroll">
            <table className="compliance-table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Who</th>
                  <th>Action</th>
                  <th>Target</th>
                  <th>Outcome</th>
                  <th>From</th>
                </tr>
              </thead>
              <tbody>
                {query.data?.events.map((event) => (
                  <AuditEventRow
                    key={event.id}
                    event={event}
                    onTarget={(id) => resetTo(() => setTargetId(id))}
                  />
                ))}
              </tbody>
            </table>
          </div>

          {query.data?.events.length === 0 && (
            <Text className="compliance-muted">
              Nothing matches. On a new deployment that is expected — the trail starts
              when somebody does something privileged.
            </Text>
          )}

          <Stack direction="horizontal" gap="condensed">
            <Button disabled={cursors.length === 0} onClick={() => setCursors((c) => c.slice(0, -1))}>
              Newer
            </Button>
            <Button
              disabled={!query.data?.next_before}
              onClick={() =>
                setCursors((c) => (query.data?.next_before ? [...c, query.data.next_before] : c))
              }
            >
              Older
            </Button>
          </Stack>
        </>
      )}
    </Stack>
  )
}

function AuditEventRow({ event, onTarget }: { event: AuditEvent; onTarget: (targetId: string) => void }) {
  const targetId = event.target_id
  return (
    <tr>
      <td className="compliance-nowrap">{new Date(event.occurred_at).toLocaleString()}</td>
      <td>
        {/* No actor is the failed sign-in case, worth naming rather than blanking. */}
        {event.actor_email ?? <Text className="compliance-muted">anonymous</Text>}
      </td>
      <td className="compliance-mono">
        {event.method} {event.route}
      </td>
      {/* The id alone, so what is shown is exactly what the target filter
          matches; the route beside it already says what kind of thing it is. */}
      <td>
        {targetId ? (
          <Button
            variant="invisible"
            size="small"
            className="compliance-target-id"
            title={`Show only actions on ${targetId}`}
            onClick={() => onTarget(targetId)}
          >
            {targetId}
          </Button>
        ) : (
          /* Most routes act on no single identified thing. */
          <Text className="compliance-muted">—</Text>
        )}
      </td>
      <td>
        <Label variant={OUTCOME_COLOUR[event.outcome]}>
          {event.outcome} {event.status_code}
        </Label>
      </td>
      <td className="compliance-mono">{event.client_host ?? ''}</td>
    </tr>
  )
}
