import { useState, useCallback, useEffect, useMemo, useRef, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useMe } from '../hooks/useMe'
import type { OrgSummary } from '../types'
import { OrgContext, type OrgContextValue } from './orgContext'
import { ACTIVE_ORG_KEY } from './storageKeys'
import { useAuth } from './useAuth'

/**
 * Owns the signed-in account's organisations and which one is active.
 *
 * The list comes from `/me`, read here once the session is known; the
 * active one is kept in localStorage so it survives a reload.
 */
export function OrgProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const { isAuthenticated, token } = useAuth()
  const me = useMe({ enabled: isAuthenticated })
  const [activeOrgId, setActiveOrgId] = useState<string | null>(
    () => localStorage.getItem(ACTIVE_ORG_KEY),
  )
  const [organizations, setOrgsState] = useState<OrgSummary[]>([])

  const switchOrg = useCallback(
    (orgId: string) => {
      localStorage.setItem(ACTIVE_ORG_KEY, orgId)
      setActiveOrgId(orgId)
      // Invalidate all queries so they refetch with the new org header
      queryClient.invalidateQueries()
    },
    [queryClient],
  )

  const applyOrganizations = useCallback((orgs: OrgSummary[], currentOrgId?: string) => {
    setOrgsState(orgs)
    const stored = localStorage.getItem(ACTIVE_ORG_KEY)
    const candidateId = currentOrgId || stored
    const isValid = candidateId && orgs.some((o) => o.id === candidateId)
    if (isValid) {
      if (candidateId !== stored) {
        localStorage.setItem(ACTIVE_ORG_KEY, candidateId)
      }
      setActiveOrgId(candidateId)
    } else if (orgs.length > 0) {
      // Auto-select first org for portal users
      localStorage.setItem(ACTIVE_ORG_KEY, orgs[0].id)
      setActiveOrgId(orgs[0].id)
    } else {
      // In no organisation: nothing to send as X-Org-Id. A stored id
      // here names one they were removed from, or one that was deleted.
      localStorage.removeItem(ACTIVE_ORG_KEY)
      setActiveOrgId(null)
    }
  }, [])

  // `/me`'s org_id is honoured once per session, on its first load, so a
  // later refetch (after a rename, say) does not override the person's
  // own choice. Keyed on the token: signing in again is a new session.
  const hintAppliedFor = useRef<string | null | undefined>(undefined)
  useEffect(() => {
    if (!me.data?.organizations) return
    const hint = hintAppliedFor.current === token ? undefined : (me.data.org_id ?? undefined)
    applyOrganizations(me.data.organizations, hint)
    hintAppliedFor.current = token
  }, [me.data, token, applyOrganizations])

  const activeOrg = useMemo(
    () => organizations.find((o) => o.id === activeOrgId) ?? null,
    [organizations, activeOrgId],
  )

  const value = useMemo<OrgContextValue>(
    () => ({ activeOrgId, activeOrg, organizations, switchOrg }),
    [activeOrgId, activeOrg, organizations, switchOrg],
  )

  return <OrgContext.Provider value={value}>{children}</OrgContext.Provider>
}
