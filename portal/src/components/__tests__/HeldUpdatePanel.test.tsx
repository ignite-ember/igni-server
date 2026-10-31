/**
 * The panel a super admin decides a held update in: an approved
 * service's new version that asks for more than the approved one. It
 * names what is added, plainly, and approves or rejects it through the
 * server — nothing outside igni server's own portal.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen, waitFor, within } from '@testing-library/react'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { HeldUpdatePanel } from '../HeldUpdatePanel'
import type { HeldExtensionUpdate } from '../../types'

const HELD: HeldExtensionUpdate = {
  client_id: 'client-codeindex',
  service: 'CodeIndex',
  extension_id: 'igni.codeindex',
  name: 'CodeIndex',
  approved_version: '1.2.0',
  version: '1.3.0',
  sha256: 'a'.repeat(64),
  uploaded_at: '2026-10-30T10:00:00Z',
  comparison: {
    wider: true,
    added: [
      { kind: 'egress', label: 'Network egress', item: 'telemetry.example', was: null },
      { kind: 'workspace', label: "Access to people's projects", item: 'write', was: 'read' },
      { kind: 'prompt_edit', label: 'Prompt section', item: 'main#rules (replace)', was: null },
    ],
  },
}

function region() {
  return screen.getByRole('region', { name: 'Update asks for more' })
}

describe('HeldUpdatePanel', () => {
  test('says which version is held, which one machines keep, and lists what it adds', () => {
    renderWithProviders(<HeldUpdatePanel held={HELD} />)

    const panel = region()
    expect(within(panel).getByText('Version 1.3.0 held')).toBeInTheDocument()
    expect(within(panel).getByText(/keep receiving version 1\.2\.0 until you decide/)).toBeInTheDocument()
    const added = within(panel).getByRole('list', { name: 'What it adds' })
    expect(Array.from(added.querySelectorAll(':scope > li'), (li) => li.textContent)).toEqual([
      'Network egress: telemetry.example',
      "Access to people's projects: write (was read)",
      'Prompt section: main#rules (replace)',
    ])
    expect(within(added).getByText('telemetry.example').tagName).toBe('CODE')
  })

  test.each([
    ['Approve', 'approve'],
    ['Reject', 'reject'],
  ])('%s calls the server for this service', async (button, decision) => {
    const called: string[] = []
    server.use(
      http.post('/v1/portal/clients/:clientId/held-update/:decision', ({ params }) => {
        called.push(`${String(params.clientId)} ${String(params.decision)}`)
        return HttpResponse.json({})
      }),
    )
    renderWithProviders(<HeldUpdatePanel held={HELD} />)

    await userEvent.click(within(region()).getByRole('button', { name: `${button} version 1.3.0 of CodeIndex` }))

    await waitFor(() => expect(called).toEqual([`client-codeindex ${decision}`]))
  })

  test("shows the server's refusal", async () => {
    server.use(
      http.post('/v1/portal/clients/:clientId/held-update/approve', () =>
        HttpResponse.json({ detail: 'another approved service already ships igni.codeindex' }, { status: 409 }),
      ),
    )
    renderWithProviders(<HeldUpdatePanel held={HELD} />)

    await userEvent.click(within(region()).getByRole('button', { name: 'Approve version 1.3.0 of CodeIndex' }))

    expect(await screen.findByText('Not changed')).toBeInTheDocument()
    expect(screen.getByText(/already ships igni\.codeindex/)).toBeInTheDocument()
  })
})
