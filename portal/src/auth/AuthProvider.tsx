import { useState, useCallback, useEffect, useMemo, type ReactNode } from 'react'
import client from '../api/client'
import { AuthContext, type AuthContextValue } from './authContext'
import { ACTIVE_ORG_KEY, TOKEN_KEY } from './storageKeys'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY))

  // Whether the server signs us in without a stored token. Null while we
  // have not asked. See the note at the top of this file: a stored key is
  // a guess about the server's opinion, and on a deployment that
  // authenticates some other way — PORTAL_AUTH_BYPASS, a cookie — the
  // guess is wrong in the direction that locks people out.
  const [serverSession, setServerSession] = useState<boolean | null>(null)

  useEffect(() => {
    // With a token there is nothing to ask: the value below is only
    // consulted when the token is absent.
    if (token) return

    let cancelled = false
    client
      .get('/portal/me')
      .then(() => {
        if (!cancelled) setServerSession(true)
      })
      .catch(() => {
        if (!cancelled) setServerSession(false)
      })

    return () => {
      cancelled = true
    }
  }, [token])

  const login = useCallback((newToken: string) => {
    localStorage.removeItem(ACTIVE_ORG_KEY)
    localStorage.setItem(TOKEN_KEY, newToken)
    setToken(newToken)
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(ACTIVE_ORG_KEY)
    setToken(null)
    setServerSession(false)
    window.location.href = '/login'
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      token,
      user: token || serverSession ? 'authenticated' : null,
      isAuthenticated: !!token || serverSession === true,
      isChecking: !token && serverSession === null,
      login,
      logout,
    }),
    [token, serverSession, login, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
