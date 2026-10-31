/**
 * Services: what registered itself with the install, the thumbprint a
 * super admin compares before approving, and the decisions — approve
 * at once, revoke and remove only after a confirmation.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen, waitFor, within } from '@testing-library/react'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { ServicesPage } from '../ServicesPage'
import { ARTIFACTS_EXTENSION } from '../../test/fixtures/serviceExtensions'
import type { RegisteredClient } from '../../types'

const ME = {
  user_id: 'boss',
  email: 'boss@acmecorp.io',
  full_name: 'The Boss',
  created_at: '2026-01-01T00:00:00Z',
  organizations: [],
  is_super_admin: true,
}

const APPROVED: RegisteredClient = {
  client_id: 'client-artifacts',
  kind: 'service',
  name: 'Artifacts',
  base_url: 'https://artifacts.acmecorp.io',
  redirect_uris: [],
  thumbprint: 'Zr9wq0ExU1t4mN8bB2xYv3cD5fG7hJ9kL1pQ3sT5uW7',
  state: 'approved',
  created_at: '2026-09-01T10:00:00Z',
  decided_at: '2026-09-01T10:05:00Z',
  extension: {
    extension_id: ARTIFACTS_EXTENSION.extension_id,
    version: ARTIFACTS_EXTENSION.version,
    name: ARTIFACTS_EXTENSION.name,
    description: ARTIFACTS_EXTENSION.description,
    published_at: ARTIFACTS_EXTENSION.published_at,
  },
}

const PENDING: RegisteredClient = {
  client_id: 'client-codeindex',
  kind: 'service',
  name: 'CodeIndex',
  base_url: 'https://codeindex.acmecorp.io',
  redirect_uris: ['https://codeindex.acmecorp.io/auth/callback'],
  thumbprint: 'NzbLsXh8uDCcd-6MNwXF4W_7noWXFZAfHkxZsRGC9Xs',
  state: 'pending',
  created_at: '2026-08-01T10:00:00Z',
  decided_at: null,
  extension: null,
}

function serve(clients: RegisteredClient[]) {
  server.use(
    http.get('/v1/portal/me', () => HttpResponse.json(ME)),
    http.get('/v1/portal/clients', () => HttpResponse.json(clients)),
    http.get('/v1/portal/extension-priority', () => HttpResponse.json({ entries: [], set_by: '', set_at: null })),
  )
}

const ORDER = {
  entries: [
    { extension_id: 'igni.artifacts', name: 'Artifacts', service: 'Artifacts' },
    { extension_id: 'igni.codeindex', name: 'CodeIndex', service: 'CodeIndex' },
  ],
  set_by: '',
  set_at: null,
}

describe('ServicesPage', () => {
  test('marks a service whose update asks for more than was approved', async () => {
    serve([{ ...APPROVED, held_version: '0.2.0' }, PENDING])

    renderWithProviders(<ServicesPage />)

    const artifacts = await screen.findByRole('listitem', { name: 'Artifacts' })
    expect(within(artifacts).getByText('Update asks for more: 0.2.0')).toBeInTheDocument()
    expect(within(screen.getByRole('listitem', { name: 'CodeIndex' })).queryByText(/Update asks for more/)).toBeNull()
    expect(screen.getByText("1 service's update asks for more than you approved")).toBeInTheDocument()
  })

  test('lists what is waiting first, with the thumbprint to compare in full', async () => {
    // Served approved-first: the page, not the server, puts the pending
    // one on top, because that is the one somebody came here to decide.
    serve([APPROVED, PENDING])

    renderWithProviders(<ServicesPage />)

    const items = await screen.findAllByRole('listitem')
    expect(items.map((item) => item.getAttribute('aria-label'))).toEqual(['CodeIndex', 'Artifacts'])

    const codeindex = items[0]
    expect(within(codeindex).getByText('Waiting for approval')).toBeInTheDocument()
    // Exactly as the service printed it — not truncated, not reformatted.
    expect(within(codeindex).getByText(PENDING.thumbprint)).toBeInTheDocument()
    expect(within(codeindex).getByText(PENDING.thumbprint).tagName).toBe('CODE')
    expect(within(codeindex).getByText(/printed in its own log/i)).toBeInTheDocument()
    expect(within(codeindex).getByText('https://codeindex.acmecorp.io/auth/callback')).toBeInTheDocument()

    expect(within(items[1]).getByText(APPROVED.thumbprint)).toBeInTheDocument()
    expect(screen.getByText('1 service is waiting for approval')).toBeInTheDocument()
  })

  test('links each service to its own website, in a new tab', async () => {
    serve([APPROVED, PENDING])

    renderWithProviders(<ServicesPage />)

    const link = await screen.findByRole('link', { name: 'https://artifacts.acmecorp.io' })
    expect(link).toHaveAttribute('href', 'https://artifacts.acmecorp.io')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  })

  test("shows what each service ships, or that it has not sent it yet", async () => {
    serve([APPROVED, PENDING])

    renderWithProviders(<ServicesPage />)

    const [codeindex, artifacts] = await screen.findAllByRole('listitem')

    // Artifacts uploaded its extension: name, version, what it does, and
    // the way to all of it.
    expect(within(artifacts).getByText('Extension: Artifacts')).toBeInTheDocument()
    expect(within(artifacts).getByText('0.1.0')).toBeInTheDocument()
    expect(within(artifacts).getByText(ARTIFACTS_EXTENSION.description)).toBeInTheDocument()
    expect(within(artifacts).getByRole('link', { name: 'See the whole extension: Artifacts' })).toHaveAttribute(
      'href',
      '/services/client-artifacts',
    )

    // CodeIndex has not: said plainly, with its own page still reachable.
    expect(within(codeindex).getByText('Has not uploaded its extension yet.')).toBeInTheDocument()
    expect(within(codeindex).queryByText(/^Extension:/)).not.toBeInTheDocument()
    expect(within(codeindex).getByRole('link', { name: 'Details: CodeIndex' })).toHaveAttribute(
      'href',
      '/services/client-codeindex',
    )
  })

  test('approves a pending service', async () => {
    serve([PENDING])
    let approved: string | null = null
    server.use(
      http.post('/v1/portal/clients/:clientId/approve', ({ params }) => {
        approved = String(params.clientId)
        return HttpResponse.json({ ...PENDING, state: 'approved', decided_at: '2026-09-02T00:00:00Z' })
      }),
    )

    renderWithProviders(<ServicesPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'Approve CodeIndex' }))

    await waitFor(() => expect(approved).toBe('client-codeindex'))
  })

  test('revokes only once the confirmation is accepted', async () => {
    serve([APPROVED])
    let revoked: string | null = null
    server.use(
      http.post('/v1/portal/clients/:clientId/revoke', ({ params }) => {
        revoked = String(params.clientId)
        return HttpResponse.json({ ...APPROVED, state: 'revoked' })
      }),
    )

    const user = userEvent.setup()
    renderWithProviders(<ServicesPage />)

    // Cancelling leaves it alone.
    await user.click(await screen.findByRole('button', { name: 'Revoke Artifacts' }))
    await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(revoked).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Revoke Artifacts' }))
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText(/within ten minutes/i)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Revoke' }))

    await waitFor(() => expect(revoked).toBe('client-artifacts'))
  })

  test('removes a registration after confirming', async () => {
    serve([PENDING])
    let removed: string | null = null
    server.use(
      http.delete('/v1/portal/clients/:clientId', ({ params }) => {
        removed = String(params.clientId)
        return new HttpResponse(null, { status: 204 })
      }),
    )

    const user = userEvent.setup()
    renderWithProviders(<ServicesPage />)
    await user.click(await screen.findByRole('button', { name: 'Remove CodeIndex' }))
    expect(removed).toBeNull()
    await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Remove' }))

    await waitFor(() => expect(removed).toBe('client-codeindex'))
  })

  test("shows the server's reason when a decision is refused", async () => {
    serve([PENDING])
    server.use(
      http.post('/v1/portal/clients/:clientId/approve', () =>
        HttpResponse.json({ detail: 'No such client' }, { status: 404 }),
      ),
    )

    renderWithProviders(<ServicesPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'Approve CodeIndex' }))

    expect(await screen.findByText('No such client')).toBeInTheDocument()
  })

  test('shows why approving is refused when another service ships the same extension', async () => {
    serve([PENDING])
    const reason = 'another approved service already ships igni.codeindex'
    server.use(
      http.post('/v1/portal/clients/:clientId/approve', () => HttpResponse.json({ detail: reason }, { status: 409 })),
    )

    renderWithProviders(<ServicesPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'Approve CodeIndex' }))

    expect(await screen.findByText('Not changed')).toBeInTheDocument()
    expect(screen.getByText(reason)).toBeInTheDocument()
  })

  test('says so when nothing has registered', async () => {
    serve([])

    renderWithProviders(<ServicesPage />)

    expect(await screen.findByText('No services have registered')).toBeInTheDocument()
  })

  test('is not for anybody else', async () => {
    server.use(http.get('/v1/portal/me', () => HttpResponse.json({ ...ME, is_super_admin: false })))

    renderWithProviders(<ServicesPage />)

    expect(await screen.findByText('Super admins only')).toBeInTheDocument()
  })

  test('sets the priority order that settles collisions between extensions', async () => {
    serve([APPROVED, PENDING])
    let saved: unknown = null
    server.use(
      http.get('/v1/portal/extension-priority', () => HttpResponse.json(ORDER)),
      http.put('/v1/portal/extension-priority', async ({ request }) => {
        saved = await request.json()
        return HttpResponse.json({
          entries: [ORDER.entries[1], ORDER.entries[0]],
          set_by: 'boss@acmecorp.io',
          set_at: '2026-10-25T09:00:00Z',
        })
      }),
    )

    renderWithProviders(<ServicesPage />)

    const order = await screen.findByLabelText('Priority of extensions')
    expect(within(order).getByText(/the order their services were approved in/)).toBeInTheDocument()
    await userEvent.click(within(order).getByRole('button', { name: 'Move CodeIndex higher' }))
    await userEvent.click(within(order).getByRole('button', { name: 'Save this order' }))

    await waitFor(() => expect(saved).toEqual({ order: ['igni.codeindex', 'igni.artifacts'] }))
    expect(await within(order).findByText(/Set by boss@acmecorp.io/)).toBeInTheDocument()
  })
})
