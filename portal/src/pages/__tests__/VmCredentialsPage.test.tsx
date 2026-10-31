/**
 * VM credentials: the working ones, by whose VM they are, and revoking one.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { VmCredentialsPage } from '../VmCredentialsPage'
import type { VmCredential } from '../../types'

const MINE: VmCredential = {
  credential_id: 'f1',
  org_id: 'demo-org-001',
  producer: { kind: 'person', id: 'demo-user-001' },
  created_at: '2026-10-26T10:00:00Z',
  last_used_at: null,
  idle_expires_at: '2027-01-24T10:00:00Z',
  awaiting_pickup: false,
  picked_up_at: null,
  generation: 3,
}

const BOTS: VmCredential = {
  ...MINE,
  credential_id: 'f2',
  producer: { kind: 'app', id: 'app-1' },
  awaiting_pickup: true,
}

describe('VmCredentialsPage', () => {
  test('lists them by whose VM they are, and revokes one', async () => {
    let revoked = ''
    server.use(
      http.get('/v1/portal/vm-credentials', () => HttpResponse.json({ credentials: [MINE, BOTS] })),
      http.get('/v1/portal/apps', () =>
        HttpResponse.json({
          apps: [
            {
              app_id: 'app-1',
              client_id: 'app-1',
              name: 'Review bot',
              owner_user_id: null,
              owner_email: null,
              thumbprint: 't',
              vm_profile_id: null,
              created_at: '2026-10-26T10:00:00Z',
            },
          ],
        }),
      ),
      http.delete('/v1/portal/vm-credentials/:id', ({ params }) => {
        revoked = String(params.id)
        return new HttpResponse(null, { status: 204 })
      }),
    )

    renderWithProviders(<VmCredentialsPage />)

    expect(await screen.findByText('You')).toBeInTheDocument()
    expect(await screen.findByText('App: Review bot')).toBeInTheDocument()
    expect(screen.getByText('waiting for its VM')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Revoke the credential of You' }))
    await expect.poll(() => revoked).toBe('f1')
  })

  test('says when there are none', async () => {
    server.use(http.get('/v1/portal/vm-credentials', () => HttpResponse.json({ credentials: [] })))

    renderWithProviders(<VmCredentialsPage />)

    expect(await screen.findByText('No VM credentials')).toBeInTheDocument()
  })
})
