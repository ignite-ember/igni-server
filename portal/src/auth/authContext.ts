/**
 * The auth context and its value type, alone in a file.
 *
 * Both lived in `AuthProvider.tsx` beside the component, which breaks
 * Vite's fast refresh: a module that exports anything other than
 * components cannot be hot-replaced, so editing the provider forced a
 * full reload and lost whatever state the page was in.
 * `react-refresh/only-export-components` had been reporting it for as
 * long as the file existed, and nothing ran eslint in CI.
 *
 * `useAuth.ts` already followed this shape — hook in its own file — so
 * this is the other half of a split that was half done.
 */

import { createContext } from 'react'

export interface AuthContextValue {
  token: string | null
  user: string | null
  isAuthenticated: boolean
  /** True until we know. Routes must wait rather than bounce to /login. */
  isChecking: boolean
  login: (token: string) => void
  logout: () => void
}

export const AuthContext = createContext<AuthContextValue | undefined>(undefined)
