/**
 * The four admin tools that had no interface.
 *
 * F105. The audit trail (F90), the access review (F92), subject access
 * (F96) and erasure (F93) were all built, tested, and reachable only
 * with `curl` and an admin token. Each one exists to answer a question
 * somebody asks under time pressure — an auditor, a regulator, a person
 * exercising a right — and "write a shell script" is not an answer at
 * that moment.
 *
 * ## One page, four tabs
 *
 * They belong together: all four are things an administrator does *about*
 * people rather than to the product, and three of the four are read-only.
 * Four sidebar entries for four rarely-used tools would crowd out the
 * ones used daily.
 *
 * The active tab lives in the URL hash, so a link to `#erasure` in a
 * runbook lands where it says.
 *
 * ## Admin-only, and the nav entry is too
 *
 * Every endpoint behind these tabs is `require_admin`. The sidebar hides
 * the whole item for a member, because the alternative — a visible link
 * that 403s — is the flaw F103 found on the login page, where three
 * provider buttons appeared whether or not the server would accept them.
 *
 * ## What is deliberately awkward
 *
 * The erasure tab asks you to type the address a second time. It edits
 * stored records in place and cannot be undone, so a
 * paste-and-click is the wrong shape for it. Everything else here is a
 * read.
 */

import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query'
import {
  Banner,
  Button,
  FormControl,
  Label,
  PageHeader,
  Select,
  Spinner,
  Stack,
  Text,
  Textarea,
  TextInput,
  UnderlineNav,
} from '@primer/react'
import {
  AlertIcon,
  DownloadIcon,
  ListUnorderedIcon,
  PeopleIcon,
  PersonIcon,
  TrashIcon,
} from '@primer/octicons-react'
import client from '../api/client'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import './CompliancePage.css'
import { countOf } from '../utils/plural'

type TabKey = 'audit' | 'access-review' | 'subject-access' | 'erasure'

const TABS = [
  { key: 'audit' as const, label: 'Audit trail', icon: ListUnorderedIcon },
  { key: 'access-review' as const, label: 'Access review', icon: PeopleIcon },
  { key: 'subject-access' as const, label: 'Subject access', icon: PersonIcon },
  { key: 'erasure' as const, label: 'Erasure', icon: TrashIcon },
]

function parseTab(hash: string): TabKey {
  const candidate = hash.replace('#', '')
  return TABS.some((t) => t.key === candidate) ? (candidate as TabKey) : 'audit'
}

/* ── Audit trail ─────────────────────────────────────────────────── */

interface AuditEvent {
  id: string
  occurred_at: string
  actor_email: string | null
  actor_user_id: string | null
  method: string
  route: string
  target_type: string | null
  target_id: string | null
  status_code: number
  outcome: string
  client_host: string | null
  detail: Record<string, unknown> | null
}

interface AuditEventPage {
  events: AuditEvent[]
  next_before: string | null
}

const OUTCOME_COLOUR: Record<string, 'success' | 'attention' | 'danger' | 'secondary'> = {
  success: 'success',
  denied: 'attention',
  error: 'danger',
}

function AuditTrailTab() {
  const [outcome, setOutcome] = useState('')
  const [targetId, setTargetId] = useState('')
  // Keyset pagination, so the cursor is a timestamp rather than a page
  // number. Kept as a stack so Back works: the server hands out the next
  // cursor and has no notion of a previous one.
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

  const resetTo = (change: () => void) => {
    // Any filter change invalidates the cursor stack: a `before` from one
    // filtered view means nothing in another.
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
          {/* Primer's `Select`, not a bare `<select>`. The first version
              was the latter, styled to match, and `FormControl` does not
              wire a label to a child it does not recognise — so the
              control had no accessible name and a screen reader
              announced nothing. Found by a test querying it by label. */}
          <Select value={outcome} onChange={(e) => resetTo(() => setOutcome(e.target.value))}>
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
            // A plain link rather than fetch-and-blob: the browser
            // streams it to disk, which matters because this can be
            // 50 000 lines.
            window.location.href = '/v1/portal/audit-events/export'
          }}
        >
          Export as JSON Lines
        </Button>
      </Stack>

      {query.isError && (
        <Banner variant="critical" title="Could not read the audit trail" />
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
                  <tr key={event.id}>
                    <td className="compliance-nowrap">
                      {new Date(event.occurred_at).toLocaleString()}
                    </td>
                    <td>
                      {/* A failed sign-in has no actor, and that is the
                          interesting case rather than a missing value. */}
                      {event.actor_email ?? <Text className="compliance-muted">anonymous</Text>}
                    </td>
                    <td className="compliance-mono">
                      {event.method} {event.route}
                    </td>
                    {/* The id alone, and clickable. This cell used to
                        concatenate the type and the id — so the text you
                        could read was `inference_node gpu-secure-03`
                        while the filter above matches `target_id`
                        exactly, and pasting what you saw returned
                        nothing.

                        The type chip that replaced it is gone too: the
                        `route` column beside this one already says what
                        kind of thing was acted on, in more detail and
                        without a second vocabulary to learn. */}
                    <td>
                      {event.target_id ? (
                        <Button
                          variant="invisible"
                          size="small"
                          className="compliance-target-id"
                          title={`Show only actions on ${event.target_id}`}
                          onClick={() => resetTo(() => setTargetId(event.target_id ?? ''))}
                        >
                          {event.target_id}
                        </Button>
                      ) : (
                        /* Not a missing value: most routes act on no
                           single identified thing. A sign-in attempt, or
                           creating something that has no id yet. */
                        <Text className="compliance-muted">—</Text>
                      )}
                    </td>
                    <td>
                      <Label variant={OUTCOME_COLOUR[event.outcome] ?? 'secondary'}>
                        {event.outcome} {event.status_code}
                      </Label>
                    </td>
                    <td className="compliance-mono">{event.client_host ?? ''}</td>
                  </tr>
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

/* ── Access review ───────────────────────────────────────────────── */

interface AccessReviewRow {
  user_id: string | null
  email: string | null
  full_name: string | null
  auth_provider: string | null
  role: string
  status: string
  granted_at: string | null
  granted_by: string | null
  last_sign_in_at: string | null
  days_since_sign_in: number | null
  flags: string[]
}

interface AccessReview {
  generated_at: string
  dormant_after_days: number
  total: number
  active: number
  needs_review: number
  rows: AccessReviewRow[]
}

const FLAG_LABEL: Record<string, { text: string; variant: 'attention' | 'danger' | 'secondary' }> = {
  invitation_pending: { text: 'Invitation pending', variant: 'secondary' },
  never_signed_in: { text: 'Never signed in', variant: 'attention' },
  dormant: { text: 'Dormant', variant: 'attention' },
  suspended: { text: 'Suspended', variant: 'danger' },
}

function AccessReviewTab() {
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

      {query.isError && <Banner variant="critical" title="Could not run the access review" />}

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
                // The index is the last resort, and it is reached: a row
                // can have neither a user id nor an address if an
                // invitation was created without one. `Math.random()`
                // was the first version — impure during render, which
                // the linter added in F104 caught immediately, and also
                // a real bug: a fresh key every render throws the row
                // away and rebuilds it.
                <tr key={row.user_id ?? row.email ?? `row-${index}`}>
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
                    {/* An unclaimed invitation has no user row at all, so
                        no provider. That is the most interesting row in
                        the table rather than an empty cell. */}
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
                      {row.flags.map((flag) => {
                        const label = FLAG_LABEL[flag] ?? { text: flag, variant: 'secondary' as const }
                        return (
                          <Label key={flag} variant={label.variant}>
                            {label.text}
                          </Label>
                        )
                      })}
                    </Stack>
                  </td>
                </tr>
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

/* ── Subject access ──────────────────────────────────────────────── */

function SubjectAccessTab() {
  const [email, setEmail] = useState('')

  const request = useMutation({
    mutationFn: (address: string) =>
      client
        .post<Record<string, unknown>>('/portal/subject-access', { email: address })
        .then((r) => r.data),
  })

  return (
    <Stack gap="normal">
      <Text size="small" className="compliance-hint">
        Everything this deployment holds about one address, with the context GDPR
        Article 15 also requires — purposes, categories, recipients, retention, source.
        Credentials are withheld and the report says which. Other contributors&apos;
        commits are excluded: answering one person&apos;s request must not disclose
        another&apos;s data.
      </Text>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (email.trim()) request.mutate(email.trim())
        }}
      >
        <Stack direction="horizontal" gap="normal" align="end">
          <FormControl>
            <FormControl.Label>Email address</FormControl.Label>
            <TextInput
              type="email"
              value={email}
              placeholder="person@example.com"
              onChange={(e) => setEmail(e.target.value)}
            />
            <FormControl.Caption>
              The subject may have no account here; the report is keyed on the address.
            </FormControl.Caption>
          </FormControl>
          <Button type="submit" variant="primary" disabled={request.isPending || !email.trim()}>
            {request.isPending ? 'Collecting…' : 'Produce report'}
          </Button>
        </Stack>
      </form>

      {request.isError && (
        <Banner variant="critical" title="Could not produce the report" />
      )}

      {request.data && (
        <Stack gap="condensed">
          <Stack direction="horizontal" gap="condensed" align="center">
            <Text weight="semibold">Report</Text>
            <Button
              size="small"
              leadingVisual={DownloadIcon}
              onClick={() => {
                const blob = new Blob([JSON.stringify(request.data, null, 2)], {
                  type: 'application/json',
                })
                const url = URL.createObjectURL(blob)
                const a = document.createElement('a')
                a.href = url
                a.download = `subject-access-${email.trim()}.json`
                a.click()
                URL.revokeObjectURL(url)
              }}
            >
              Download
            </Button>
          </Stack>
          {/* The whole document, rather than a rendering of the parts we
              happen to know about. A subject access response is a legal
              artefact: showing a summary and hiding the rest would be
              the wrong default, and the shape changes as the schema
              does. */}
          <Textarea
            readOnly
            rows={24}
            className="compliance-report"
            value={JSON.stringify(request.data, null, 2)}
            aria-label="Subject access report"
          />
        </Stack>
      )}
    </Stack>
  )
}

/* ── Erasure ─────────────────────────────────────────────────────── */

interface ErasureResult {
  email: string
  events_redacted: number
  logins_included: number
}

function ErasureTab() {
  const [email, setEmail] = useState('')
  const [confirmation, setConfirmation] = useState('')

  const erase = useMutation({
    mutationFn: (address: string) =>
      client
        .post<ErasureResult>('/portal/erasure', { email: address })
        .then((r) => r.data),
  })

  const matches = email.trim() !== '' && email.trim() === confirmation.trim()

  return (
    <Stack gap="normal">
      <Banner
        variant="warning"
        title="This rewrites stored data and cannot be undone"
        description={
          'Redacts one person from the raw provider payloads igni keeps — their address, ' +
          'their name where it sits beside it, and their known provider logins. Every ' +
          'other contributor in the same events is left intact, so history stays ' +
          'readable. GDPR Article 17.'
        }
      />

      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (matches) erase.mutate(email.trim())
        }}
      >
        <Stack gap="normal">
          <FormControl>
            <FormControl.Label>Email address to redact</FormControl.Label>
            <TextInput
              type="email"
              value={email}
              placeholder="person@example.com"
              onChange={(e) => setEmail(e.target.value)}
            />
          </FormControl>

          <FormControl>
            <FormControl.Label>Type it again to confirm</FormControl.Label>
            <TextInput
              type="email"
              value={confirmation}
              onChange={(e) => setConfirmation(e.target.value)}
            />
            <FormControl.Caption>
              {/* Typing it twice is not ceremony. The other three tabs
                  are reads; this one edits stored rows in place, and a
                  pasted address that is one character wrong redacts
                  nobody while reporting success. */}
              A wrong address here redacts nobody and reports success, so it is worth
              the second look.
            </FormControl.Caption>
          </FormControl>

          <Stack direction="horizontal" gap="condensed" align="center">
            <Button
              type="submit"
              variant="danger"
              leadingVisual={AlertIcon}
              disabled={!matches || erase.isPending}
            >
              {erase.isPending ? 'Redacting…' : 'Redact from stored payloads'}
            </Button>
            {email.trim() && !matches && (
              <Text size="small" className="compliance-muted">
                The two addresses do not match yet.
              </Text>
            )}
          </Stack>
        </Stack>
      </form>

      {erase.isError && <Banner variant="critical" title="The redaction did not run" />}

      {erase.data && (
        <Banner
          variant={erase.data.events_redacted > 0 ? 'success' : 'info'}
          title={
            erase.data.events_redacted > 0
              ? `Redacted from ${countOf(erase.data.events_redacted, "stored event")}`
              : 'Nothing to redact'
          }
          description={
            erase.data.events_redacted > 0
              ? `Matched on the address and ${countOf(
                  erase.data.logins_included,
                  'known provider login',
                )}.`
              : 'No stored payload mentions that address. Check the spelling — this is also what a ' +
                'correct request looks like when the payloads have already been pruned by retention.'
          }
        />
      )}
    </Stack>
  )
}

/* ── The page ────────────────────────────────────────────────────── */

export function CompliancePage() {
  useDocumentTitle('Compliance', 'Audit trail, access review, subject access and erasure.')
  const location = useLocation()
  const navigate = useNavigate()

  // Derived from the hash rather than stored beside it. Two sources of
  // truth for one fact is what F104 had to unpick.
  const tab = parseTab(location.hash)

  return (
    <Stack gap="spacious">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.Title as="h1">Compliance</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text>
            The four things an auditor, a regulator or a person exercising a right will
            ask for. Everything here stays in this deployment.
          </Text>
        </PageHeader.Description>
      </PageHeader>

      {/* `UnderlineNav`. Hand-rolled buttons were the first version and
          were worse for no gain: this one handles the keyboard and the
          current-page semantics. */}
      <UnderlineNav aria-label="Compliance sections">
        {TABS.map((entry) => (
          <UnderlineNav.Item
            key={entry.key}
            icon={entry.icon}
            aria-current={tab === entry.key ? 'page' : undefined}
            onSelect={(e) => {
              e.preventDefault()
              navigate(`/compliance#${entry.key}`, { replace: true })
            }}
          >
            {entry.label}
          </UnderlineNav.Item>
        ))}
      </UnderlineNav>

      {tab === 'audit' && <AuditTrailTab />}
      {tab === 'access-review' && <AccessReviewTab />}
      {tab === 'subject-access' && <SubjectAccessTab />}
      {tab === 'erasure' && <ErasureTab />}
    </Stack>
  )
}
