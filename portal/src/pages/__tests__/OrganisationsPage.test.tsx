/**
 * Every organisation on the install, for super admins: what each owns,
 * adding a domain, and the server's refusal to remove one in use shown
 * as it is.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { OrganisationsPage } from '../OrganisationsPage'

const ME = {
  user_id: 'boss',
  email: 'boss@acmecorp.io',
  full_name: 'The Boss',
  created_at: '2026-01-01T00:00:00Z',
  organizations: [{ id: 'o1', slug: 'acme', display_name: 'Acme', role: 'admin' }],
  is_super_admin: true,
}

const ACME = { id: 'o1', slug: 'acme', display_name: 'Acme', domains: ['acmecorp.io'], members: 3 }

describe('OrganisationsPage', () => {
  test('lists each organisation with its domains and members', async () => {
    server.use(
      http.get('/v1/portal/me', () => HttpResponse.json(ME)),
      http.get('/v1/portal/deployment/organizations', () => HttpResponse.json([ACME])),
    )

    renderWithProviders(<OrganisationsPage />)

    expect(await screen.findByText('Acme')).toBeInTheDocument()
    expect(screen.getByText('acmecorp.io')).toBeInTheDocument()
    expect(screen.getByText(/3 members/)).toBeInTheDocument()
  })

  test('adds a domain', async () => {
    let added: unknown = null
    server.use(
      http.get('/v1/portal/me', () => HttpResponse.json(ME)),
      http.get('/v1/portal/deployment/organizations', () => HttpResponse.json([ACME])),
      http.post('/v1/portal/organizations/o1/domains', async ({ request }) => {
        added = await request.json()
        return HttpResponse.json({ org_id: 'o1', domains: ['acme-uk.io', 'acmecorp.io'] })
      }),
    )

    renderWithProviders(<OrganisationsPage />)
    await userEvent.type(await screen.findByLabelText('Add a domain to Acme'), 'acme-uk.io')
    await userEvent.click(screen.getByRole('button', { name: 'Add domain' }))

    await expect.poll(() => added).toEqual({ domain: 'acme-uk.io' })
  })

  test("shows why a domain in use cannot be removed", async () => {
    server.use(
      http.get('/v1/portal/me', () => HttpResponse.json(ME)),
      http.get('/v1/portal/deployment/organizations', () => HttpResponse.json([ACME])),
      http.delete('/v1/portal/organizations/o1/domains/:domain', () =>
        HttpResponse.json({ detail: 'Members of this organisation use acmecorp.io. Remove them first.' }, { status: 409 }),
      ),
    )

    renderWithProviders(<OrganisationsPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'Remove acmecorp.io from Acme' }))

    expect(await screen.findByText('Members of this organisation use acmecorp.io. Remove them first.')).toBeInTheDocument()
  })

  test('is not for anybody else', async () => {
    server.use(http.get('/v1/portal/me', () => HttpResponse.json({ ...ME, is_super_admin: false })))

    renderWithProviders(<OrganisationsPage />)

    expect(await screen.findByText('Super admins only')).toBeInTheDocument()
  })
})
