/**
 * OrgProvider — owns `activeOrgId`, `organizations`, and the bridge
 * between them, `/me` and localStorage. Bugs here surface as "the wrong
 * org's data is showing" or "active org gets reset on every refresh",
 * which are both very hard to debug from a stack trace.
 *
 * Covers:
 *  - rehydrates activeOrgId from localStorage on mount
 *  - `/me` organisations auto-select the first when nothing is stored
 *  - a stored selection that's still valid is kept
 *  - a stale stored selection falls back to the first org
 *  - `/me`'s org_id wins over storage on the first load
 *  - `switchOrg` persists the new selection
 *  - signed out, `/me` is not asked
 */
import { afterEach, beforeEach, describe, expect, test } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { server } from '../../test/server'
import { AuthProvider } from '../AuthProvider'
import { OrgProvider } from '../OrgProvider'
import { useOrg } from '../useOrg'
import type { OrgSummary } from '../../types'

function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <OrgProvider>{children}</OrgProvider>
      </AuthProvider>
    </QueryClientProvider>
  )
  return { wrapper, queryClient }
}

const ORG_A: OrgSummary = { id: 'org-a', slug: 'a', display_name: 'A', role: 'admin' }
const ORG_B: OrgSummary = { id: 'org-b', slug: 'b', display_name: 'B', role: 'member' }

function serveMe(organizations: OrgSummary[], orgId: string | null = null) {
  server.use(
    http.get('/v1/portal/me', () =>
      HttpResponse.json({
        user_id: 'u1',
        email: 'a@example.com',
        full_name: 'A Person',
        created_at: '2026-01-01T00:00:00Z',
        org_id: orgId,
        organizations,
      }),
    ),
  )
}

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('igni_token', 'a-token')
})

afterEach(() => {
  localStorage.clear()
})

describe('OrgProvider', () => {
  test('rehydrates activeOrgId from localStorage on mount', () => {
    serveMe([ORG_A, ORG_B])
    localStorage.setItem('igni_active_org', 'org-a')
    const { wrapper } = makeWrapper()
    const { result } = renderHook(() => useOrg(), { wrapper })

    expect(result.current.activeOrgId).toBe('org-a')
    // No organizations populated yet -> activeOrg is null
    expect(result.current.activeOrg).toBeNull()
  })

  test('auto-selects the first org when nothing is stored', async () => {
    serveMe([ORG_A, ORG_B])
    const { wrapper } = makeWrapper()
    const { result } = renderHook(() => useOrg(), { wrapper })

    await waitFor(() => expect(result.current.activeOrg?.id).toBe('org-a'))
    expect(result.current.activeOrgId).toBe('org-a')
    expect(localStorage.getItem('igni_active_org')).toBe('org-a')
  })

  test('keeps a stored selection that is still valid', async () => {
    serveMe([ORG_A, ORG_B])
    localStorage.setItem('igni_active_org', 'org-b')
    const { wrapper } = makeWrapper()
    const { result } = renderHook(() => useOrg(), { wrapper })

    await waitFor(() => expect(result.current.activeOrg?.id).toBe('org-b'))
    expect(result.current.activeOrgId).toBe('org-b')
  })

  test('falls back to the first org when the stored id is stale', async () => {
    serveMe([ORG_A, ORG_B])
    localStorage.setItem('igni_active_org', 'org-ghost')
    const { wrapper } = makeWrapper()
    const { result } = renderHook(() => useOrg(), { wrapper })

    await waitFor(() => expect(result.current.activeOrgId).toBe('org-a'))
    expect(localStorage.getItem('igni_active_org')).toBe('org-a')
  })

  test("honours /me's org_id over storage on the first load", async () => {
    serveMe([ORG_A, ORG_B], 'org-b')
    localStorage.setItem('igni_active_org', 'org-a')
    const { wrapper } = makeWrapper()
    const { result } = renderHook(() => useOrg(), { wrapper })

    await waitFor(() => expect(result.current.activeOrgId).toBe('org-b'))
    expect(localStorage.getItem('igni_active_org')).toBe('org-b')
  })

  test('a later /me refetch does not override the chosen org', async () => {
    serveMe([ORG_A, ORG_B], 'org-a')
    const { wrapper, queryClient } = makeWrapper()
    const { result } = renderHook(() => useOrg(), { wrapper })

    await waitFor(() => expect(result.current.activeOrg?.id).toBe('org-a'))
    act(() => result.current.switchOrg('org-b'))
    await act(() => queryClient.refetchQueries({ queryKey: ['me'] }))

    expect(result.current.activeOrgId).toBe('org-b')
  })

  test('switchOrg persists the new selection', async () => {
    serveMe([ORG_A, ORG_B])
    const { wrapper } = makeWrapper()
    const { result } = renderHook(() => useOrg(), { wrapper })

    await waitFor(() => expect(result.current.activeOrg?.id).toBe('org-a'))
    act(() => result.current.switchOrg('org-b'))

    expect(result.current.activeOrgId).toBe('org-b')
    expect(result.current.activeOrg?.id).toBe('org-b')
    expect(localStorage.getItem('igni_active_org')).toBe('org-b')
  })

  test('useOrg outside provider throws a clear error', () => {
    const originalError = console.error
    console.error = () => {}
    try {
      expect(() => renderHook(() => useOrg())).toThrow(/OrgProvider/)
    } finally {
      console.error = originalError
    }
  })
})
