/** The current organisation. Throws outside an `OrgProvider`. */

import { useContext } from 'react'

import { OrgContext, type OrgContextValue } from './orgContext'

export function useOrg(): OrgContextValue {
  const ctx = useContext(OrgContext)
  if (!ctx) throw new Error('useOrg must be used within OrgProvider')
  return ctx
}
