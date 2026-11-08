import { useQuery } from '@tanstack/react-query'
import client from '../api/client'
import type { LicenceNotice } from '../types'

/**
 * `GET /portal/licence/notice`, shared by the banner and the licensee
 * line so a page load asks once. An hour fresh: a licence changes when
 * somebody installs one, and installing it here refreshes this.
 */
export function useLicenceNotice() {
  return useQuery({
    queryKey: ['licence-notice'],
    queryFn: () => client.get<LicenceNotice>('/portal/licence/notice').then((r) => r.data),
    staleTime: 60 * 60 * 1000,
  })
}
