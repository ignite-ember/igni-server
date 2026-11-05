/**
 * DashboardPage — read-only summary. Tests the three read paths
 * (/portal/me, /portal/usage, /portal/team)
 * compose into the stat cards + chart correctly.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { DashboardPage } from '../DashboardPage'

const ME = {
  user_id: 'u1',
  email: 'alex@ember.sh',
  full_name: 'Alex Chen',
  created_at: '2026-01-01T00:00:00Z',
  role: 'admin',
  org_id: 'org-1',
  organizations: [{ id: 'org-1', slug: 'demo', display_name: 'Demo', role: 'admin' }],
}

function useDashboardHandlers(opts?: { usageEmpty?: boolean; usageError?: boolean }) {
  server.use(
    http.get('/v1/portal/me', () => HttpResponse.json(ME)),
    http.get('/v1/portal/usage', () => {
      if (opts?.usageError) return HttpResponse.json({ detail: 'boom' }, { status: 500 })
      return HttpResponse.json({
        data: opts?.usageEmpty
          ? []
          : [{ date: '2026-05-01', input_tokens: 1000, output_tokens: 500, total_tokens: 1500, request_count: 7 }],
        total_tokens: 1_500_000,
        total_input_tokens: 1_000_000,
        total_output_tokens: 500_000,
        total_requests: 42,
      })
    }),
    http.get('/v1/portal/team', () =>
      HttpResponse.json({
        members: [
          { id: 'u1', user_id: 'u1', email: ME.email, full_name: 'Alex', role: 'admin', tier: 'pro', status: 'active', total_requests: 0, total_tokens: 0, last_active: null, joined_at: '2026-01-01T00:00:00Z', assigned_by: null },
          { id: 'u2', user_id: 'u2', email: 'b@x.com', full_name: 'Bob', role: 'member', tier: 'lite', status: 'active', total_requests: 0, total_tokens: 0, last_active: null, joined_at: '2026-02-01T00:00:00Z', assigned_by: 'u1' },
          { id: 'u3', user_id: 'u3', email: 'i@x.com', full_name: null, role: 'member', tier: 'lite', status: 'invited', total_requests: 0, total_tokens: 0, last_active: null, joined_at: '2026-03-01T00:00:00Z', assigned_by: 'u1' },
        ],
        total: 3,
      }),
    ),
  )
}

describe('DashboardPage', () => {
  test('renders personalized welcome message', async () => {
    useDashboardHandlers()
    renderWithProviders(<DashboardPage />)
    expect(await screen.findByText(/Welcome, Alex Chen/)).toBeInTheDocument()
  })

  test('renders all three stat cards with computed values', async () => {
    useDashboardHandlers()
    renderWithProviders(<DashboardPage />)

    // Total Tokens 1.5M
    expect(await screen.findByText('Total Tokens')).toBeInTheDocument()
    expect(screen.getByText(/1\.5M/)).toBeInTheDocument()
    // "Requests" appears twice — once as the stat card label, once as
    // the per-day chart heading. Both renderings carry the same string,
    // so a bare ``getByText`` is ambiguous. Assert at least one node
    // shows up and the stat value 42 is on the page.
    expect(screen.getAllByText('Requests').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('42')).toBeInTheDocument()
    // Team Members — 2 active (Alex + Bob)
    expect(screen.getByText('Team Members')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
  })

  test('renders empty-state when no usage data exists', async () => {
    useDashboardHandlers({ usageEmpty: true })
    renderWithProviders(<DashboardPage />)

    expect(await screen.findByText(/No usage data yet/i)).toBeInTheDocument()
  })

  test('shows partial-load warning banner when a query fails', async () => {
    useDashboardHandlers({ usageError: true })
    renderWithProviders(<DashboardPage />)

    expect(await screen.findByText(/Some data could not be loaded/i)).toBeInTheDocument()
  })
})
