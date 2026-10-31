/**
 * The Compliance entry is admin-only.
 *
 * Every endpoint behind that page is `require_admin`, so a member who can
 * see the link gets a 403 for clicking something the portal offered — the
 * flaw F103 found on the login page, where three provider buttons showed
 * whether or not the server would accept them. Hiding it is the fix; this
 * keeps it hidden.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, waitFor } from '@testing-library/react'
import { renderWithProviders } from '../../../test/render'
import { server } from '../../../test/server'
import { Sidebar } from '../Sidebar'

function meResponse(role: 'admin' | 'member') {
  return {
    user_id: 'u1',
    email: 'someone@example.com',
    full_name: 'Some One',
    created_at: '2026-01-01T00:00:00Z',
    role,
    pending_email: null,
    auth_provider: 'email',
    org_id: 'o1',
    org_slug: 'acme',
    org_display_name: 'Acme',
    organizations: [{ id: 'o1', slug: 'acme', display_name: 'Acme', role }],
  }
}

/**
 * Read off the nav's own text rather than with `findByText`.
 *
 * `findByText('Team')` resolved and then failed `toBeInTheDocument`: the
 * node it returned had been replaced by a re-render between the query
 * and the assertion. The nav element itself is stable, so waiting on its
 * content avoids racing the render — and asserting Compliance's absence
 * against the same string is one fact rather than two queries.
 */
async function navText(): Promise<() => string> {
  const nav = await screen.findByRole('navigation', { name: /Main navigation/i })
  return () => nav.textContent ?? ''
}

describe('the Compliance nav entry', () => {
  test('an admin is offered it', async () => {
    server.use(http.get('/v1/portal/me', () => HttpResponse.json(meResponse('admin'))))

    renderWithProviders(<Sidebar />)
    const text = await navText()

    await waitFor(() => expect(text()).toContain('Compliance'))
  })

  test('a member is not', async () => {
    server.use(http.get('/v1/portal/me', () => HttpResponse.json(meResponse('member'))))

    renderWithProviders(<Sidebar />)
    const text = await navText()

    // Waits for something a member does see, so this cannot pass merely
    // because the nav has not populated yet.
    await waitFor(() => expect(text()).toContain('Team'))
    expect(text()).not.toContain('Compliance')
  })
})
