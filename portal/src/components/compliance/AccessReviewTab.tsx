import { useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Banner, FormControl, Label, Spinner, Stack, Text, TextInput } from '@primer/react'

import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import type { AccessFlag, AccessReview, AccessReviewRow } from '../../types'

const FLAG_LABEL: Record<AccessFlag, { text: string; variant: 'attention' | 'danger' | 'secondary' }> = {
  invitation_pending: { text: 'Invitation pending', variant: 'secondary' },
  never_signed_in: { text: 'Never signed in', variant: 'attention' },
  dormant: { text: 'Dormant', variant: 'attention' },
  suspended: { text: 'Suspended', variant: 'danger' },
}

/** Who has access, how they got it, and whether they are using it. */
export function AccessReviewTab() {
  const [days, setDays] = useState(90)

  const query = useQuery({
    queryKey: ['access-review', days],
    placeholderData: keepPreviousData,
    queryFn: () =>
      client
        .get<AccessReview>(`/portal/access-review?dormant_after_days=${days}`)
        .then((r) => r.data),
  })

  return (
    <Stack gap="normal">
      <Text size="small" className="compliance-hint">
        Who has access, how they got it, and whether they are using it. SOC 2 CC6.3 and
        ISO 27001 A 5.18 both ask for this periodically; the answer is meant to be
        boring.
      </Text>

      <Stack direction="horizontal" gap="normal" align="end">
        <FormControl>
          <FormControl.Label>Dormant after</FormControl.Label>
          <TextInput
            type="number"
            min={1}
            value={days}
            trailingVisual="days"
            onChange={(e) => setDays(Math.max(1, Number(e.target.value) || 1))}
          />
        </FormControl>

        {query.data && (
          <Stack direction="horizontal" gap="condensed" align="center">
            {/* count-ok: "with access" is not the noun being counted. */}
            <Label variant="secondary">{query.data.total} with access</Label>
            <Label variant="success">{query.data.active} active</Label>
            <Label variant={query.data.needs_review > 0 ? 'attention' : 'secondary'}>
              {query.data.needs_review} need review
            </Label>
          </Stack>
        )}
      </Stack>

      {query.isError && (
        <Banner
          variant="critical"
          title="Could not run the access review"
          description={refusalOf(query.error)}
        />
      )}

      {query.isLoading ? (
        <Spinner />
      ) : (
        <div className="compliance-table-scroll">
          <table className="compliance-table">
            <thead>
              <tr>
                <th>Person</th>
                <th>Role</th>
                <th>How they sign in</th>
                <th>Granted</th>
                <th>Last sign-in</th>
                <th>Flags</th>
              </tr>
            </thead>
            <tbody>
              {query.data?.rows.map((row, index) => (
                // The index is reached: an invitation created without an
                // address has neither a user id nor an email.
                <AccessRow key={row.user_id ?? row.email ?? `row-${index}`} row={row} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {query.data && (
        <Text size="small" className="compliance-muted">
          Generated {new Date(query.data.generated_at).toLocaleString()}. Dormant means no
          sign-in for {query.data.dormant_after_days} days.
        </Text>
      )}
    </Stack>
  )
}

function AccessRow({ row }: { row: AccessReviewRow }) {
  return (
    <tr>
      <td>
        <Stack gap="none">
          <Text>{row.full_name ?? row.email ?? 'unknown'}</Text>
          {row.full_name && row.email && (
            <Text size="small" className="compliance-muted">
              {row.email}
            </Text>
          )}
        </Stack>
      </td>
      <td>{row.role}</td>
      <td>
        {/* An unclaimed invitation has no user, so no provider. */}
        {row.auth_provider ?? <Text className="compliance-muted">—</Text>}
      </td>
      <td className="compliance-nowrap">
        {row.granted_at ? new Date(row.granted_at).toLocaleDateString() : '—'}
      </td>
      <td className="compliance-nowrap">
        {row.last_sign_in_at
          ? `${new Date(row.last_sign_in_at).toLocaleDateString()} (${row.days_since_sign_in}d)`
          : '—'}
      </td>
      <td>
        <Stack direction="horizontal" gap="none" wrap="wrap">
          {row.flags.map((flag) => (
            <Label key={flag} variant={FLAG_LABEL[flag].variant}>
              {FLAG_LABEL[flag].text}
            </Label>
          ))}
        </Stack>
      </td>
    </tr>
  )
}
