/**
 * Commands in VMs: running one in a VM, queueing a job for a group, and
 * the audit log of what ran where.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { VmCommandsPage } from '../VmCommandsPage'
import { OVERVIEW } from '../../test/fixtures/vmProfiles'
import type { VmCommandRecord } from '../../types'

const FINISHED: VmCommandRecord = {
  command_id: 'c1',
  org_id: 'demo-org-001',
  producer: { kind: 'person', id: 'demo-user-001' },
  command: 'apt-get install -y jq',
  timeout_s: 600,
  job_id: null,
  wake: true,
  requested_by: { kind: 'person', id: 'demo-user-001', acting_as: 'producer' },
  state: 'finished',
  handed_to: 'node-1',
  created_at: '2026-10-26T10:00:00Z',
  handed_out_at: '2026-10-26T10:00:01Z',
  started_at: '2026-10-26T10:00:02Z',
  finished_at: '2026-10-26T10:00:05Z',
  exit_code: 0,
  output: '',
  error: null,
}

function serve() {
  server.use(
    http.get('/v1/vm/commands', () => HttpResponse.json({ commands: [FINISHED] })),
    http.get('/v1/vm/jobs', () => HttpResponse.json({ jobs: [] })),
    http.get('/v1/portal/apps', () => HttpResponse.json({ apps: [] })),
    http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () => HttpResponse.json(OVERVIEW)),
    http.get('/v1/portal/groups', () =>
      HttpResponse.json({ groups: [{ id: 'g-legal', name: 'Legal' }], total: 1 }),
    ),
  )
}

describe('VmCommandsPage', () => {
  test('shows the audit log', async () => {
    serve()

    renderWithProviders(<VmCommandsPage />)

    expect(await screen.findByText('apt-get install -y jq')).toBeInTheDocument()
    expect(screen.getByText('exit 0')).toBeInTheDocument()
    expect(screen.getByText(/in their own VM/)).toBeInTheDocument()
  })

  test('runs a command in my own VM', async () => {
    serve()
    let sent: unknown = null
    server.use(
      http.post('/v1/vm/commands', async ({ request }) => {
        sent = await request.json()
        return HttpResponse.json(FINISHED, { status: 202 })
      }),
    )

    renderWithProviders(<VmCommandsPage />)
    const form = await screen.findByRole('form', { name: 'Run a command' })
    await userEvent.type(form.querySelector('input')!, 'echo hi')
    await userEvent.click(screen.getByRole('button', { name: 'Run' }))

    await expect.poll(() => sent).toEqual({ producer: { kind: 'person', id: 'demo-user-001' }, command: 'echo hi' })
  })

  test('queues a job for a group', async () => {
    serve()
    let sent: unknown = null
    server.use(
      http.post('/v1/vm/jobs', async ({ request }) => {
        sent = await request.json()
        return HttpResponse.json({ jobs: [] }, { status: 202 })
      }),
    )

    renderWithProviders(<VmCommandsPage />)
    const form = await screen.findByRole('form', { name: 'Queue a job' })
    await screen.findByRole('option', { name: 'Legal' })
    const [, group] = form.querySelectorAll('select')
    await userEvent.selectOptions(group, 'g-legal')
    await userEvent.type(form.querySelector('input')!, 'pip install x')
    await userEvent.click(screen.getByRole('button', { name: 'Queue job' }))

    await expect.poll(() => sent).toEqual({ scope: { kind: 'group', id: 'g-legal' }, command: 'pip install x' })
  })
})
