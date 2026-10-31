/**
 * The org context and its value type, alone in a file.
 *
 * Same reason as `authContext.ts`: a module exporting a hook alongside a
 * component cannot be hot-replaced, so editing `OrgProvider` forced a
 * full page reload. Moving the context here lets both the provider and
 * `useOrg` import it.
 */

import { createContext } from 'react'

import type { OrgSummary } from '../types'

export interface OrgContextValue {
  activeOrgId: string | null
  activeOrg: OrgSummary | null
  organizations: OrgSummary[]
  switchOrg: (orgId: string) => void
  setOrganizations: (orgs: OrgSummary[], currentOrgId?: string) => void
}

export const OrgContext = createContext<OrgContextValue | undefined>(undefined)
