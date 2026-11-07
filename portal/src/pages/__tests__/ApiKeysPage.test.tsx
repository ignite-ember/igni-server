/**
 * Personal API keys: making one shows it once; the list never does; revoking one.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { ApiKeysPage } from '../ApiKeysPage'
import type { ApiKey, CreatedApiKey } from '../../types'

const NIGHTLY: ApiKey = {
  key_id: 'k1',
  name: 'Nightly review',
  user_id: 'demo-user-001',
  created_at: '2026-11-01T10:00:00Z',
  expires_at: '2027-01-30T10:00:00Z',
  last_used_at: null,
  expired: false,
}

describe('ApiKeysPage', () => {
  test('makes a key, shows it once, and lists it without it', async () => {
    let made: unknown = null
    const created: CreatedApiKey = { ...NIGHTLY, key_id: 'k2', name: 'CI', api_key: 'igpak1.org.k2.s3cr3t' }
    let listed: ApiKey[] = []
    server.use(
      http.get('/v1/portal/api-keys', () => HttpResponse.json({ keys: listed })),
      http.post('/v1/portal/api-keys', async ({ request }) => {
        made = await request.json()
        listed = [{ ...NIGHTLY, key_id: created.key_id, name: created.name }]
        return HttpResponse.json(created, { status: 201 })
      }),
    )

    renderWithProviders(<ApiKeysPage />)

    expect(await screen.findByText('No API keys')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText(/What it is for/), 'CI')
    await userEvent.click(screen.getByRole('button', { name: 'Make key' }))

    expect(await screen.findByDisplayValue('igpak1.org.k2.s3cr3t')).toBeInTheDocument()
    expect(screen.getByText(/only time the key is shown/)).toBeInTheDocument()
    expect(made).toEqual({ name: 'CI', expires_in_days: 90 })
    expect(await screen.findByRole('rowheader', { name: 'CI' })).toBeInTheDocument()
  })

  test('asks what a key is for before making one', async () => {
    server.use(http.get('/v1/portal/api-keys', () => HttpResponse.json({ keys: [] })))

    renderWithProviders(<ApiKeysPage />)

    await userEvent.click(await screen.findByRole('button', { name: 'Make key' }))
    expect(await screen.findByText(/Say which program it is for/)).toBeInTheDocument()
  })

  test('revokes a key, and marks an expired one', async () => {
    let revoked = ''
    server.use(
      http.get('/v1/portal/api-keys', () =>
        HttpResponse.json({ keys: [NIGHTLY, { ...NIGHTLY, key_id: 'k3', name: 'Old', expired: true }] }),
      ),
      http.delete('/v1/portal/api-keys/:id', ({ params }) => {
        revoked = String(params.id)
        return new HttpResponse(null, { status: 204 })
      }),
    )

    renderWithProviders(<ApiKeysPage />)

    expect(await screen.findByText('expired')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Revoke the key “Nightly review”' }))
    await expect.poll(() => revoked).toBe('k1')
  })
})
