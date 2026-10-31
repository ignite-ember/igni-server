/**
 * Apps: an org admin registers one with a name, an owner and the app's
 * public key — never a private one — and sees it listed.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { AppsPage } from '../AppsPage'
import { parsePublicJwks } from '../../lib/publicJwks'
import type { OrgApp } from '../../types'

const BOT: OrgApp = {
  app_id: 'app-1',
  client_id: 'app-1',
  name: 'Review bot',
  owner_user_id: 'demo-user-001',
  owner_email: 'alex@ember.sh',
  thumbprint: 'thumb-1',
  vm_profile_id: null,
  created_at: '2026-10-26T10:00:00Z',
}

const PUBLIC_KEY = '{"kty": "RSA", "n": "abc", "e": "AQAB", "kid": "k1"}'

describe('AppsPage', () => {
  test('lists the apps', async () => {
    server.use(http.get('/v1/portal/apps', () => HttpResponse.json({ apps: [BOT] })))

    renderWithProviders(<AppsPage />)

    expect(await screen.findByRole('link', { name: 'Review bot' })).toHaveAttribute('href', '/apps/app-1')
    expect(screen.getByText('thumb-1')).toBeInTheDocument()
  })

  test('registers one with its public key', async () => {
    let sent: unknown = null
    server.use(
      http.get('/v1/portal/apps', () => HttpResponse.json({ apps: [] })),
      http.post('/v1/portal/apps', async ({ request }) => {
        sent = await request.json()
        return HttpResponse.json(BOT, { status: 201 })
      }),
    )

    renderWithProviders(<AppsPage />)
    await userEvent.type(await screen.findByRole('textbox', { name: /Name/ }), 'Review bot')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: /Owner/ }), await screen.findByRole('option', { name: 'Alex Chen' }))
    await userEvent.click(screen.getByRole('textbox', { name: /Public key/ }))
    await userEvent.paste(PUBLIC_KEY)
    await userEvent.click(screen.getByRole('button', { name: 'Register' }))

    await expect.poll(() => sent).toEqual({
      name: 'Review bot',
      owner_user_id: 'demo-user-001',
      jwks: { keys: [{ kty: 'RSA', n: 'abc', e: 'AQAB', kid: 'k1' }] },
    })
    expect(await screen.findByText('Review bot is registered')).toBeInTheDocument()
  })

  test('is for org admins only', async () => {
    server.use(
      http.get('/v1/portal/me', () =>
        HttpResponse.json({ user_id: 'm', email: 'm@x.io', full_name: null, created_at: null, role: 'member' }),
      ),
    )

    renderWithProviders(<AppsPage />)

    expect(await screen.findByText('Organisation admins only')).toBeInTheDocument()
  })
})

describe('parsePublicJwks', () => {
  test('takes one JWK or a set', () => {
    expect(parsePublicJwks(PUBLIC_KEY)).toEqual({ keys: [{ kty: 'RSA', n: 'abc', e: 'AQAB', kid: 'k1' }] })
    expect(parsePublicJwks(`{"keys": [${PUBLIC_KEY}]}`)).toEqual({
      keys: [{ kty: 'RSA', n: 'abc', e: 'AQAB', kid: 'k1' }],
    })
  })

  test('refuses a private key and anything that is not JSON', () => {
    expect(parsePublicJwks('{"kty": "RSA", "d": "secret"}')).toMatch(/private key/)
    expect(parsePublicJwks('not json')).toMatch(/JSON/)
  })
})
