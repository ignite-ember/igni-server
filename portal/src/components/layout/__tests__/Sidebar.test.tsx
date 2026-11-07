/**
 * Sidebar gating — the Rename / Delete org actions live inside the
 * org-switcher ActionMenu and must only show up when the active org's role
 * is "admin". Create organization is a super admin's: an install holds one
 * or two organisations and people join them by invitation.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen, within } from '@testing-library/react'
import { renderWithProviders } from '../../../test/render'
import { server } from '../../../test/server'
import { Sidebar } from '../Sidebar'

function meResponse(role: 'admin' | 'member', isSuperAdmin = false) {
  return {
    is_super_admin: isSuperAdmin,
    user_id: 'demo-user-001',
    email: 'alex@ember.sh',
    full_name: 'Alex Chen',
    created_at: '2026-01-01T00:00:00Z',
    role: 'admin',
    pending_email: null,
    auth_provider: 'email',
    org_id: 'demo-org-001',
    org_slug: 'demo',
    org_display_name: 'Demo Org',
    organizations: [
      { id: 'demo-org-001', slug: 'demo', display_name: 'Demo Org', role },
    ],
  }
}

describe('Sidebar', () => {
  test('admin sees Rename action in org switcher', async () => {
    server.use(http.get('/v1/portal/me', () => HttpResponse.json(meResponse('admin'))))

    renderWithProviders(<Sidebar />)

    const orgButton = await screen.findByRole('button', { name: /Switch organization/i })
    // Wait for /me to resolve and OrgProvider to populate activeOrg.
    await screen.findByText('Demo Org')
    await userEvent.click(orgButton)

    const menu = await screen.findByRole('menu')
    expect(within(menu).getByText(/Rename/i)).toBeInTheDocument()
    // Their only organisation can be deleted: being in none is a normal
    // state now, not one every account must be kept out of.
    expect(within(menu).getByText(/^Delete$/i)).toBeInTheDocument()
    // An org admin is not a super admin, so cannot create organisations.
    expect(within(menu).queryByText(/Create organization/i)).not.toBeInTheDocument()
  })

  test('member does NOT see Rename or Delete actions', async () => {
    server.use(http.get('/v1/portal/me', () => HttpResponse.json(meResponse('member'))))

    renderWithProviders(<Sidebar />)

    const orgButton = await screen.findByRole('button', { name: /Switch organization/i })
    await screen.findByText('Demo Org')
    await userEvent.click(orgButton)

    const menu = await screen.findByRole('menu')
    expect(within(menu).queryByText(/Rename/i)).not.toBeInTheDocument()
    expect(within(menu).queryByText(/^Delete$/i)).not.toBeInTheDocument()
    expect(within(menu).queryByText(/Create organization/i)).not.toBeInTheDocument()
  })

  test('a super admin can create organisations and sees Deployment', async () => {
    server.use(http.get('/v1/portal/me', () => HttpResponse.json(meResponse('member', true))))

    renderWithProviders(<Sidebar />)

    const orgButton = await screen.findByRole('button', { name: /Switch organization/i })
    await screen.findByText('Demo Org')
    expect(screen.getByRole('link', { name: /Super admins/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Services/i })).toHaveAttribute('href', '/services')
    expect(screen.getByRole('link', { name: /^Licence$/ })).toHaveAttribute('href', '/licence')
    await userEvent.click(orgButton)

    const menu = await screen.findByRole('menu')
    expect(within(menu).getByText(/Create organization/i)).toBeInTheDocument()
  })

  test('nobody else sees Deployment', async () => {
    server.use(http.get('/v1/portal/me', () => HttpResponse.json(meResponse('admin'))))

    renderWithProviders(<Sidebar />)

    await screen.findByText('Demo Org')
    expect(screen.queryByRole('link', { name: /Super admins/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Services/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /^Licence$/ })).not.toBeInTheDocument()
  })

  test('names the licensee at its foot, for everybody', async () => {
    server.use(
      http.get('/v1/portal/me', () => HttpResponse.json(meResponse('member'))),
      http.get('/v1/portal/licence/notice', () =>
        HttpResponse.json({ warnings: [], customer: 'Example Bank N.V.', licence_id: 'IE-2026-0042' }),
      ),
    )

    renderWithProviders(<Sidebar />)

    expect(await screen.findByText(/Licensed to Example Bank N\.V\./)).toBeInTheDocument()
  })
})
