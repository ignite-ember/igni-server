/**
 * GroupsPage tests — covers the observable shape of the groups list.
 */
import { afterEach, describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen, cleanup } from '@testing-library/react'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { GroupsPage } from '../GroupsPage'

afterEach(() => {
  cleanup()
  server.resetHandlers()
})

const GROUPS_DATA = {
  groups: [
    {
      id: 'g-1',
      org_id: 'demo-org-001',
      name: 'Engineering',
      description: 'The engineering team',
      created_by_user_id: 'demo-user-001',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      member_count: 3,
      entry_count: 2,
    },
    {
      id: 'g-2',
      org_id: 'demo-org-001',
      name: 'Contractors',
      description: null,
      created_by_user_id: 'demo-user-001',
      created_at: '2026-02-01T00:00:00Z',
      updated_at: '2026-02-01T00:00:00Z',
      member_count: 1,
      entry_count: 0,
    },
  ],
  total: 2,
}

describe('GroupsPage', () => {
  test('renders groups list', async () => {
    server.use(
      http.get('/v1/portal/groups', () => HttpResponse.json(GROUPS_DATA)),
    )
    renderWithProviders(<GroupsPage />, { route: '/groups' })
    expect(await screen.findByText('Engineering')).toBeInTheDocument()
    expect(screen.getByText('Contractors')).toBeInTheDocument()
  })

  test('non-admin sees empty state without New group button', async () => {
    server.use(
      http.get('/v1/portal/me', () =>
        HttpResponse.json({
          user_id: 'demo-user-001',
          email: 'me@ember.sh',
          full_name: 'Me',
          created_at: '2026-01-01T00:00:00Z',
          role: 'member',
          pending_email: null,
          auth_provider: 'email',
          org_id: 'demo-org-001',
          org_slug: 'demo',
          org_display_name: 'Demo Org',
          organizations: [
            { id: 'demo-org-001', slug: 'demo', display_name: 'Demo Org', role: 'member' },
          ],
        }),
      ),
    )
    renderWithProviders(<GroupsPage />, { route: '/groups' })
    expect(await screen.findByText(/no groups yet/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /new group/i })).not.toBeInTheDocument()
  })

  test('kebab opens with view + delete options', async () => {
    server.use(
      http.get('/v1/portal/groups', () => HttpResponse.json(GROUPS_DATA)),
    )
    renderWithProviders(<GroupsPage />, { route: '/groups' })
    await screen.findByText('Engineering')
    await userEvent.click(screen.getByRole('button', { name: /Actions for Engineering/i }))
    expect(screen.getByRole('menu')).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /view details/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /delete/i })).toBeInTheDocument()
  })

  test('a refused delete tells the admin why', async () => {
    server.use(
      http.get('/v1/portal/groups', () => HttpResponse.json(GROUPS_DATA)),
      http.delete('/v1/portal/groups/g-1', () =>
        HttpResponse.json({ detail: 'This group still owns entries in use.' }, { status: 409 }),
      ),
    )
    renderWithProviders(<GroupsPage />, { route: '/groups' })
    await screen.findByText('Engineering')
    await userEvent.click(screen.getByRole('button', { name: /Actions for Engineering/i }))
    await userEvent.click(screen.getByRole('menuitem', { name: /delete/i }))
    await userEvent.click(await screen.findByRole('button', { name: /^delete$/i }))

    expect(await screen.findByText('This group still owns entries in use.')).toBeInTheDocument()
  })

  test('admin sees Create first group button in empty state', async () => {
    server.use(
      http.get('/v1/portal/me', () =>
        HttpResponse.json({
          user_id: 'demo-user-001',
          email: 'me@ember.sh',
          full_name: 'Me',
          created_at: '2026-01-01T00:00:00Z',
          role: 'admin',
          pending_email: null,
          auth_provider: 'email',
          org_id: 'demo-org-001',
          org_slug: 'demo',
          org_display_name: 'Demo Org',
          organizations: [
            { id: 'demo-org-001', slug: 'demo', display_name: 'Demo Org', role: 'admin' },
          ],
        }),
      ),
      http.get('/v1/portal/groups', () => HttpResponse.json({ groups: [], total: 0 })),
    )
    renderWithProviders(<GroupsPage />, { route: '/groups' })
    expect(await screen.findByText(/no groups yet/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /create first group/i })).toHaveAttribute('href', '/groups/new')
  })
})

describe('what a group does to its people', () => {
  /**
   * A group is exactly what its members get, so the one thing the list
   * cannot show is the entry content. The model it pins can be, and was
   * previously invisible until you opened the group.
   */
  function withGroups(over: Record<string, unknown>) {
    server.use(
      http.get('/v1/portal/groups', () =>
        HttpResponse.json({
          groups: [{ ...GROUPS_DATA.groups[0], ...over }],
          total: 1,
        }),
      ),
    )
    renderWithProviders(<GroupsPage />, { route: '/groups' })
  }

  test('the pinned model is on the row', async () => {
    withGroups({ default_model: 'legal-reviewer' })

    expect(await screen.findByText('legal-reviewer')).toBeInTheDocument()
  })


  test('a group with no model pinned stays quiet', async () => {
    withGroups({ default_model: null })

    await screen.findByText('Engineering')
    expect(screen.queryByText('legal-reviewer')).not.toBeInTheDocument()
  })
})
