import { useQuery } from '@tanstack/react-query'

import client from '../../api/client'
import type { AppHold, LegalHoldList } from '../../types'

/** The legal hold standing on one app, or null; read from the organisation's holds. */
export function useAppHold(orgId: string, appId: string): AppHold | null {
  const holds = useQuery({
    queryKey: ['legal-holds', orgId],
    queryFn: () => client.get<LegalHoldList>(`/portal/orgs/${orgId}/legal-holds`).then((r) => r.data),
    enabled: !!orgId,
  })
  return holds.data?.apps.find((h) => h.app_id === appId) ?? null
}
