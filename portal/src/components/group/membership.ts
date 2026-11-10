import type { QueryClient } from '@tanstack/react-query'

/**
 * Everything that shows who is in which group, after somebody moved.
 *
 * A move changes two groups' member lists, both groups' counts and the
 * organisation-wide memberships, so all of them are refetched.
 */
export function invalidateMembership(queryClient: QueryClient): void {
  queryClient.invalidateQueries({ queryKey: ['group-members'] })
  queryClient.invalidateQueries({ queryKey: ['group-memberships'] })
  queryClient.invalidateQueries({ queryKey: ['group'] })
  queryClient.invalidateQueries({ queryKey: ['groups'] })
}
