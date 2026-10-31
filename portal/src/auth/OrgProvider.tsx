import { useState, useCallback, useMemo, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { OrgSummary } from '../types'
import { OrgContext, type OrgContextValue } from './orgContext'
import { ACTIVE_ORG_KEY } from './storageKeys'



export function OrgProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
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

  const setOrganizations = useCallback(
    (orgs: OrgSummary[], currentOrgId?: string) => {
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
    },
    [],
  )

  const activeOrg = useMemo(
    () => organizations.find((o) => o.id === activeOrgId) ?? null,
    [organizations, activeOrgId],
  )

  const value = useMemo<OrgContextValue>(
    () => ({ activeOrgId, activeOrg, organizations, switchOrg, setOrganizations }),
    [activeOrgId, activeOrg, organizations, switchOrg, setOrganizations],
  )

  return <OrgContext.Provider value={value}>{children}</OrgContext.Provider>
}
