import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import client from '../api/client'

const SPENT_CODE =
  'That sign-in link has already been used or has expired. Please sign in again.'

/**
 * Trades the one-time code a provider sent the browser back with for a
 * token. It is exchanged over POST rather than the token arriving in the
 * URL — see `app/db/redis/auth_handoff.py` for what that URL used to
 * leak into.
 *
 * A query rather than a mutation, and that is the load-bearing detail.
 * `main.tsx` renders under `StrictMode`, which mounts, unmounts and
 * remounts on the first render, so component state and `useRef` are
 * both recreated and an effect guarded by either posts the code twice.
 * The code is single-use, so the second post gets a correct 400 and
 * paints an error over a sign-in that worked. The query cache lives in
 * the injected `QueryClient`, outside the component, and dedupes the
 * in-flight request by its key — so the code is posted once. Measured:
 * the StrictMode test counts the posts.
 *
 * `onToken` runs once, when the token arrives. Returns the sentence to
 * show if the exchange failed, or `null`.
 */
export function useSignInCodeExchange(
  code: string | null,
  onToken: (token: string) => void,
): string | null {
  const exchange = useQuery({
    queryKey: ['auth-exchange', code],
    queryFn: async () =>
      (await client.post<{ access_token: string }>('/auth/exchange', { code })).data.access_token,
    enabled: code !== null,
    // Single-use: asking again can only ever be refused.
    retry: false,
    retryOnMount: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })

  // Storing the token and navigating are both changes to things outside
  // React, which is what an effect is for.
  const token = exchange.data
  useEffect(() => {
    if (token) onToken(token)
  }, [token, onToken])

  // Single-use and thirty seconds long, so the realistic causes are a
  // reloaded callback page or a bookmarked one. Both mean "sign in
  // again", which is what this says.
  return exchange.isError ? SPENT_CODE : null
}
