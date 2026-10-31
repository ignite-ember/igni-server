/**
 * An account in no organisation sees why instead of an org-scoped page.
 *
 * Accounts start in none and join one by invitation; only a super admin
 * creates one. Pages about the account or the install still open.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { renderWithProviders } from '../../../test/render'
import { server } from '../../../test/server'
import { AppShell } from '../AppShell'

function me(isSuperAdmin: boolean, organizations: unknown[] = []) {
  return {
    user_id: 'u1',
    email: 'new@acmecorp.io',
    full_name: 'New Person',
    created_at: '2026-01-01T00:00:00Z',
    org_id: null,
    organizations,
    is_super_admin: isSuperAdmin,
  }
}

function renderAt(route: string) {
  return renderWithProviders(
    <Routes>
      <Route element={<AppShell />}>
        <Route path="dashboard" element={<p>the dashboard</p>} />
        <Route path="settings" element={<p>the settings</p>} />
      </Route>
    </Routes>,
    { route },
  )
}

describe('AppShell with no organisation', () => {
  test('an org-scoped page says who to ask', async () => {
    server.use(http.get('/v1/portal/me', () => HttpResponse.json(me(false))))

    renderAt('/dashboard')

    expect(await screen.findByText(/not in an organisation yet/i)).toBeInTheDocument()
    expect(screen.getByText(/Ask an admin of your organisation to invite you/i)).toBeInTheDocument()
    expect(screen.queryByText('the dashboard')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Create organization' })).not.toBeInTheDocument()
  })

  test('the bootstrap account on a fresh install is offered to create the organisation', async () => {
    server.use(
      http.get('/v1/portal/me', () => HttpResponse.json(me(true))),
      http.get('/v1/portal/deployment/organizations', () => HttpResponse.json([])),
    )

    renderAt('/dashboard')

    expect(await screen.findByRole('button', { name: 'Create organization' })).toBeInTheDocument()
  })

  test('once the organisation exists, it moves onto a company address', async () => {
    const sent: unknown[] = []
    server.use(
      http.get('/v1/portal/me', () => HttpResponse.json(me(true))),
      http.get('/v1/portal/deployment/organizations', () =>
        HttpResponse.json([{ id: 'o1', slug: 'acme', display_name: 'Acme', domains: ['acmecorp.io'], members: 0 }]),
      ),
      http.post('/v1/portal/me/company-address/code', async ({ request }) => {
        sent.push(await request.json())
        return HttpResponse.json({ message: 'Verification code has been sent to it@acmecorp.io' })
      }),
      http.post('/v1/portal/me/company-address', async ({ request }) => {
        sent.push(await request.json())
        return HttpResponse.json(me(false))
      }),
    )

    renderAt('/dashboard')

    expect(await screen.findByRole('link', { name: /make a colleague a super admin/i })).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Your company address'), 'it@acmecorp.io')
    await userEvent.click(screen.getByRole('button', { name: 'Send code' }))
    await userEvent.type(await screen.findByLabelText('Code'), '123456')
    await userEvent.click(screen.getByRole('button', { name: 'Use this address' }))

    await expect.poll(() => sent).toEqual([
      { email: 'it@acmecorp.io' },
      { email: 'it@acmecorp.io', code: '123456' },
    ])
  })

  test('the account settings still open', async () => {
    server.use(http.get('/v1/portal/me', () => HttpResponse.json(me(false))))

    renderAt('/settings')

    expect(await screen.findByText('the settings')).toBeInTheDocument()
  })

  test('somebody in an organisation sees the page', async () => {
    server.use(
      http.get('/v1/portal/me', () =>
        HttpResponse.json(me(false, [{ id: 'o1', slug: 'corp', display_name: 'Corp', role: 'member' }])),
      ),
    )

    renderAt('/dashboard')

    expect(await screen.findByText('the dashboard')).toBeInTheDocument()
  })
})
