/**
 * The deployment's licence: whose it is, where each paid module stands,
 * and installing a new one.
 *
 * Ignite Ember protects its paid modules with a signed licence file, a
 * contract and traceability, not DRM. igni server does not enforce the
 * licence — each module verifies the same file itself — so this page
 * reports and installs; it decides nothing (docs/licence-file.md).
 *
 * ## Super admins only, and so is the nav entry
 *
 * `GET` and `PUT /portal/licence` are `require_super_admin`. The entry
 * sits in the sidebar's Deployment group with Organisations and Super
 * admins, shown only to a super admin, for the reason the Compliance
 * entry is hidden from a member: a link that 403s is the flaw F103 found.
 *
 * ## Installing
 *
 * Choose the `.json` file Ignite Ember sent, or paste it. Choosing a file
 * puts its contents in the box, so what is about to be sent is on the
 * screen. igni server verifies it against the keys built into this
 * release before writing anything, and the two refusals are shown in
 * words an admin can act on: a `422` says why the file is not a licence
 * this release accepts, and a `409` says the file is good but the licence
 * is a mounted Secret, which is where it has to go instead.
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Banner, Button, FormControl, Heading, Label, PageHeader, Stack, Text, Textarea } from '@primer/react'
import { Blankslate, DataTable, Table } from '@primer/react/experimental'
import { LawIcon } from '@primer/octicons-react'
import client from '../api/client'
import { refusalOf } from '../api/refusal'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import type { LicenceKind, LicenceModuleStatus, LicenceOverview, LicenceState, LicensedModule, UserMe } from '../types'
import './LicencePage.css'

const MODULE_NAMES: Record<LicensedModule, string> = {
  codeindex: 'CodeIndex',
  artifacts: 'Artifacts',
  fleet: 'Fleet',
  factory: 'Factory',
  autopilot: 'Autopilot',
}

const KIND_NAMES: Record<LicenceKind, string> = {
  commercial: 'Commercial',
  evaluation: 'Evaluation',
}

const STATES: Record<LicenceState, { label: string; variant: 'success' | 'attention' | 'severe' | 'secondary' }> = {
  valid: { label: 'Valid', variant: 'success' },
  grace: { label: 'Grace period', variant: 'attention' },
  read_only: { label: 'Read-only', variant: 'severe' },
  unlicensed: { label: 'Not licensed', variant: 'secondary' },
}

/**
 * What to do about each reason a file is refused (`app.services.licence.Reason`).
 * The server's sentence names the reason; this says what to do next.
 */
const WHAT_TO_DO: Record<string, string> = {
  malformed: 'The file is damaged or is not an igni licence. Ask Ignite Ember to send it again.',
  unknown_key:
    'It was signed with a key this release does not know: a licence for another release, or a test licence. Ask Ignite Ember for a licence for this release.',
  invalid_signature:
    'It has been changed since Ignite Ember signed it, even by a character. Install the file exactly as it was sent.',
}

/**
 * The day a licence date falls on. In UTC, because the licence's dates
 * are UTC midnights: read in a time zone west of Greenwich, 29 October
 * would show as the 28th.
 */
function day(value: string | null): string {
  return value ? new Date(value).toLocaleDateString(undefined, { dateStyle: 'medium', timeZone: 'UTC' }) : '—'
}

interface Refusal {
  title: string
  message: string
}

/** Why an upload was not installed, in words an admin can act on. */
function refusalOfInstall(error: unknown): Refusal {
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response
  const detail = response?.data?.detail
  if (response?.status === 422 && Array.isArray(detail)) {
    // FastAPI's own 422: the body is not shaped like a licence file at all.
    return {
      title: 'This is not a licence file',
      message:
        'A licence file has four fields: igni_licence, key_id, payload and signature. Check you chose the file Ignite Ember sent.',
    }
  }
  // igni server's own refusals: `{reason, message}`, the message written for a person.
  const { reason, message } = (typeof detail === 'object' && detail !== null ? detail : {}) as {
    reason?: unknown
    message?: unknown
  }
  if (typeof message !== 'string') return { title: 'Not installed', message: refusalOf(error) }
  if (response?.status === 409) return { title: 'The licence is valid, but was not installed', message }
  const next = typeof reason === 'string' ? WHAT_TO_DO[reason] : undefined
  return { title: 'Not installed', message: next ? `${message} ${next}` : message }
}

function Licensee({ overview }: { overview: LicenceOverview }) {
  if (!overview.installed) {
    return (
      <Text as="p" className="licence-hint">
        No licence is installed. The free base needs none; each paid module needs one. Install the file Ignite Ember
        sent below.
      </Text>
    )
  }
  if (overview.licence_id === null) {
    // A file is there, and it does not verify: the modules treat it as no licence at all.
    const first = overview.modules.find((status) => status.message)
    return (
      <Banner
        variant="critical"
        title="The installed file is not a valid licence"
        description={`${first?.message ?? 'It could not be verified.'} Install the file exactly as Ignite Ember sent it.`}
      />
    )
  }
  return (
    <dl className="licence-facts" aria-label="Installed licence">
      <dt>Licensed to</dt>
      <dd>{overview.customer}</dd>
      <dt>Licence ID</dt>
      <dd className="licence-mono">{overview.licence_id}</dd>
      {overview.kind && (
        <>
          <dt>Kind</dt>
          <dd>{KIND_NAMES[overview.kind]}</dd>
        </>
      )}
      {overview.issued && (
        <>
          <dt>Issued</dt>
          <dd>{day(overview.issued)}</dd>
        </>
      )}
      <dt>File</dt>
      <dd className="licence-mono">{overview.path}</dd>
    </dl>
  )
}

function Modules({ modules }: { modules: LicenceModuleStatus[] }) {
  return (
    <Table.Container>
      <Table.Title id="licence-modules">Paid modules</Table.Title>
      <DataTable
        aria-labelledby="licence-modules"
        data={modules.map((status) => ({ id: status.module, ...status }))}
        columns={[
          { header: 'Module', field: 'module', rowHeader: true, renderCell: (row) => MODULE_NAMES[row.module] },
          {
            header: 'State',
            field: 'state',
            renderCell: (row) => <Label variant={STATES[row.state].variant}>{STATES[row.state].label}</Label>,
          },
          { header: 'Expires', field: 'expires', renderCell: (row) => day(row.expires) },
          {
            header: 'Users',
            field: 'users',
            // The Factory is not sold by the person, so a licensed one has no band.
            renderCell: (row) => (row.users !== null ? row.users : row.state === 'unlicensed' ? '—' : 'Not banded'),
          },
          { header: 'Message', field: 'message', renderCell: (row) => row.message ?? '' },
        ]}
      />
    </Table.Container>
  )
}

function Install() {
  const queryClient = useQueryClient()
  const [text, setText] = useState('')
  const [problem, setProblem] = useState<Refusal | null>(null)
  const [installed, setInstalled] = useState<LicenceOverview | null>(null)

  const install = useMutation({
    mutationFn: (envelope: unknown) => client.put<LicenceOverview>('/portal/licence', envelope).then((r) => r.data),
    onSuccess: (overview) => {
      queryClient.setQueryData(['licence'], overview)
      // The banner and the licensee line everybody sees.
      queryClient.invalidateQueries({ queryKey: ['licence-notice'] })
      setText('')
      setInstalled(overview)
    },
    onError: (error) => setProblem(refusalOfInstall(error)),
  })

  const submit = () => {
    setProblem(null)
    setInstalled(null)
    let envelope: unknown
    try {
      envelope = JSON.parse(text)
    } catch {
      setProblem({
        title: 'Not installed',
        message: 'That is not JSON. Choose the .json file, or paste all of it, from the first { to the last }.',
      })
      return
    }
    install.mutate(envelope)
  }

  return (
    <form
      aria-label="Install a licence"
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
    >
      <Stack gap="normal">
        <Heading as="h2" variant="small">
          Install a licence
        </Heading>
        <Text as="p" className="licence-hint">
          Renewing is installing the new file. It is verified before anything is written, and each module picks it up
          within a minute; nothing restarts.
        </Text>

        {problem && <Banner variant="critical" title={problem.title} description={problem.message} />}
        {installed && (
          <Banner
            variant="success"
            title="Installed"
            description={`Licence ${installed.licence_id} for ${installed.customer} is installed.`}
          />
        )}

        {/* A native file input: FormControl labels it through the shared id. */}
        <FormControl id="licence-file">
          <FormControl.Label>Licence file</FormControl.Label>
          <FormControl.Caption>The .json file Ignite Ember sent.</FormControl.Caption>
          <input
            id="licence-file"
            type="file"
            accept=".json,application/json"
            onChange={(e) => {
              const file = e.currentTarget.files?.[0]
              if (!file) return
              const reader = new FileReader()
              reader.onload = () => setText(String(reader.result))
              reader.onerror = () => setProblem({ title: 'Not read', message: 'Could not read that file.' })
              reader.readAsText(file)
            }}
          />
        </FormControl>

        <FormControl>
          <FormControl.Label>Or paste its contents</FormControl.Label>
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={6}
            block
            className="licence-paste"
            placeholder='{"igni_licence": 1, "key_id": "…", "payload": "…", "signature": "…"}'
          />
        </FormControl>

        <div>
          <Button type="submit" variant="primary" disabled={!text.trim() || install.isPending}>
            {install.isPending ? 'Installing…' : 'Install licence'}
          </Button>
        </div>
      </Stack>
    </form>
  )
}

export function LicencePage() {
  useDocumentTitle('Licence')

  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  })
  const overview = useQuery({
    queryKey: ['licence'],
    queryFn: () => client.get<LicenceOverview>('/portal/licence').then((r) => r.data),
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

  return (
    <Stack gap="spacious">
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.LeadingVisual>
            <LawIcon />
          </PageHeader.LeadingVisual>
          <PageHeader.Title>Licence</PageHeader.Title>
        </PageHeader.TitleArea>
        <PageHeader.Description>
          <Text>
            The signed file that licenses the paid modules. Each module checks it itself; everybody signed in sees whose
            it is.
          </Text>
        </PageHeader.Description>
      </PageHeader>

      {overview.isError && (
        <Banner variant="critical" title="Could not load the licence" description={refusalOf(overview.error)} />
      )}

      {overview.data && (
        <Stack gap="normal">
          <Licensee overview={overview.data} />
          <Modules modules={overview.data.modules} />
        </Stack>
      )}

      <Install />
    </Stack>
  )
}
