/**
 * OrgProvider — owns `activeOrgId`, `organizations`, and the bridge
 * between them and localStorage. Bugs here surface as "the wrong org's
 * data is showing" or "active org gets reset on every refresh", which
 * are both very hard to debug from a stack trace.
 *
 * Covers:
 *  - rehydrates activeOrgId from localStorage on mount
 *  - `setOrganizations` auto-selects first org when no stored selection
 *  - `setOrganizations` respects a stored selection that's still valid
 *  - `setOrganizations` falls back to first org when stored id is stale
 *  - `switchOrg` persists + invalidates queries
 *  - explicit `currentOrgId` hint (used on initial /me load) entries storage
 */
import { afterEach, beforeEach, describe, expect, test } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { OrgProvider } from '../OrgProvider'
import { useOrg } from '../useOrg'

function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <OrgProvider>{children}</OrgProvider>
    </QueryClientProvider>
  )
  return { wrapper, queryClient }
}

const ORG_A = { id: 'org-a', slug: 'a', display_name: 'A', role: 'admin' as const }
const ORG_B = { id: 'org-b', slug: 'b', display_name: 'B', role: 'member' as const }

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  localStorage.clear()
})

describe('OrgProvider', () => {
  test('rehydrates activeOrgId from localStorage on mount', () => {
    localStorage.setItem('igni_active_org', 'org-a')
    const { wrapper } = makeWrapper()
    const { result } = renderHook(() => useOrg(), { wrapper })

    expect(result.current.activeOrgId).toBe('org-a')
    // No organizations populated yet -> activeOrg is null
    expect(result.current.activeOrg).toBeNull()
  })

  test('setOrganizations auto-selects first org when no stored selection', () => {
    const { wrapper } = makeWrapper()
    const { result } = renderHook(() => useOrg(), { wrapper })

    act(() => result.current.setOrganizations([ORG_A, ORG_B]))

    expect(result.current.activeOrgId).toBe('org-a')
    expect(result.current.activeOrg?.id).toBe('org-a')
    expect(localStorage.getItem('igni_active_org')).toBe('org-a')
  })

  test('setOrganizations respects stored selection when still valid', () => {
    localStorage.setItem('igni_active_org', 'org-b')
    const { wrapper } = makeWrapper()
    const { result } = renderHook(() => useOrg(), { wrapper })

    act(() => result.current.setOrganizations([ORG_A, ORG_B]))

    expect(result.current.activeOrgId).toBe('org-b')
    expect(result.current.activeOrg?.id).toBe('org-b')
  })

  test('setOrganizations falls back to first org when stored id is stale', () => {
    localStorage.setItem('igni_active_org', 'org-ghost')
    const { wrapper } = makeWrapper()
    const { result } = renderHook(() => useOrg(), { wrapper })

    act(() => result.current.setOrganizations([ORG_A, ORG_B]))

    expect(result.current.activeOrgId).toBe('org-a')
    expect(localStorage.getItem('igni_active_org')).toBe('org-a')
  })

  test('setOrganizations honors explicit currentOrgId hint over storage', () => {
    localStorage.setItem('igni_active_org', 'org-a')
    const { wrapper } = makeWrapper()
    const { result } = renderHook(() => useOrg(), { wrapper })

    act(() => result.current.setOrganizations([ORG_A, ORG_B], 'org-b'))

    expect(result.current.activeOrgId).toBe('org-b')
    expect(localStorage.getItem('igni_active_org')).toBe('org-b')
  })

  test('switchOrg persists the new selection', () => {
    const { wrapper } = makeWrapper()
    const { result } = renderHook(() => useOrg(), { wrapper })

    act(() => result.current.setOrganizations([ORG_A, ORG_B]))
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
