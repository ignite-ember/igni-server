/**
 * AuthProvider — owns the `igni_token` lifecycle and the global
 * authenticated-vs-not flag that every protected route hangs off of.
 *
 * Covers:
 *  - hydrates token from localStorage on mount
 *  - `login()` writes token + clears any stale active-org selection
 *  - `logout()` clears token + active-org + redirects to /login
 *  - `useAuth()` outside an AuthProvider throws (so a missing provider is
 *    a loud failure, not a silent null)
 */
import { afterEach, beforeEach, describe, expect, test } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import type { ReactNode } from 'react'
import { AuthProvider } from '../AuthProvider'
import { useAuth } from '../useAuth'

// Same window.location proxy strategy as api/client tests — we need to
// observe writes to `location.href` without jsdom actually navigating.
const hrefHistory: string[] = []
const stubLocation = {
  href: 'http://localhost/',
  origin: 'http://localhost',
  protocol: 'http:',
  host: 'localhost',
  hostname: 'localhost',
  port: '',
  pathname: '/',
  search: '',
  hash: '',
  assign: () => {},
  replace: () => {},
  reload: () => {},
} as unknown as Location

Object.defineProperty(window, 'location', {
  configurable: true,
  writable: true,
  value: new Proxy(stubLocation, {
    set(target, prop: string, value) {
      if (prop === 'href') hrefHistory.push(value)
      ;(target as unknown as Record<string, unknown>)[prop] = value
      return true
    },
  }),
})

const wrapper = ({ children }: { children: ReactNode }) => <AuthProvider>{children}</AuthProvider>

beforeEach(() => {
  localStorage.clear()
  hrefHistory.length = 0
  ;(stubLocation as { href: string }).href = 'http://localhost/'
})

afterEach(() => {
  localStorage.clear()
})

describe('AuthProvider', () => {
  test('hydrates token from localStorage on mount', () => {
    localStorage.setItem('igni_token', 'stored-tok')
    const { result } = renderHook(() => useAuth(), { wrapper })

    expect(result.current.token).toBe('stored-tok')
    expect(result.current.isAuthenticated).toBe(true)
  })

  test('starts unauthenticated when localStorage is empty', () => {
    const { result } = renderHook(() => useAuth(), { wrapper })

    expect(result.current.token).toBeNull()
    expect(result.current.isAuthenticated).toBe(false)
    expect(result.current.user).toBeNull()
  })

  test('login() persists token AND clears stale active-org', () => {
    localStorage.setItem('igni_active_org', 'org-old')

    const { result } = renderHook(() => useAuth(), { wrapper })

    act(() => result.current.login('fresh-tok'))

    expect(result.current.token).toBe('fresh-tok')
    expect(result.current.isAuthenticated).toBe(true)
    expect(localStorage.getItem('igni_token')).toBe('fresh-tok')
    // Active-org must be cleared so the new user doesn't inherit it
    expect(localStorage.getItem('igni_active_org')).toBeNull()
  })

  test('logout() clears token + active-org + redirects to /login', () => {
    localStorage.setItem('igni_token', 'tok')
    localStorage.setItem('igni_active_org', 'org-1')

    const { result } = renderHook(() => useAuth(), { wrapper })
    expect(result.current.isAuthenticated).toBe(true)

    act(() => result.current.logout())

    expect(result.current.token).toBeNull()
    expect(result.current.isAuthenticated).toBe(false)
    expect(localStorage.getItem('igni_token')).toBeNull()
    expect(localStorage.getItem('igni_active_org')).toBeNull()
    expect(hrefHistory).toContain('/login')
  })

  test('useAuth() outside provider throws a clear error', () => {
    // Suppress the React error boundary noise for this expected throw.
    const originalError = console.error
    console.error = () => {}
    try {
      expect(() => renderHook(() => useAuth())).toThrow(/AuthProvider/)
    } finally {
      console.error = originalError
    }
  })
})
