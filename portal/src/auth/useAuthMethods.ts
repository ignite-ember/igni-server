import { useQuery } from '@tanstack/react-query'
import client from '../api/client'

export interface AuthMethods {
  password: boolean
  oauth_providers: string[]
  sso: { enabled: boolean; label: string; start_url: string }
}

/**
 * `/auth/methods`: what this deployment offers, asked once a session.
 * It changes when somebody edits the deployment's configuration and
 * restarts it, so there is no reason to ask again.
 */
export function useAuthMethods() {
  return useQuery<AuthMethods>({
    queryKey: ['auth-methods'],
    queryFn: async () => (await client.get<AuthMethods>('/auth/methods')).data,
    staleTime: Infinity,
  })
}
