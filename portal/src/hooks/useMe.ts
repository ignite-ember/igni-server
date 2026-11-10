import { useQuery } from '@tanstack/react-query'

import client from '../api/client'
import type { UserMe } from '../types'

/** The signed-in account, shared by everything that reads `['me']`. */
export function useMe({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
    staleTime: 5 * 60 * 1000,
    enabled,
  })
}
