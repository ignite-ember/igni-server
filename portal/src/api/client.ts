import axios from 'axios'
import { ACTIVE_ORG_KEY, TOKEN_KEY } from '../auth/storageKeys'

const client = axios.create({
  baseURL: '/v1',
})

client.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY) || 'bypass'
  config.headers.Authorization = `Bearer ${token}`

  const orgId = localStorage.getItem(ACTIVE_ORG_KEY)
  if (orgId) {
    config.headers['X-Org-Id'] = orgId
  }

  return config
})

/**
 * Routes that authenticate *this* session.
 *
 * The portal's token is a portal token. A 401 from `/portal/*` therefore
 * means the server rejected us and the session is over. A 401 from
 * anywhere else means that route wanted different credentials — the
 * `/chat/*` and `/api/*` endpoints authenticate a CLI user's JWT, and
 * asking them with a portal session is a mistake in the caller, not an
 * expired login.
 *
 * Treating the two alike is not theoretical: a component that called
 * `/chat/models` to populate a dropdown logged every admin out of the
 * group page, on a request they never made and could not see.
 */
const SESSION_ROUTES = /^\/portal(\/|$)/

function isSessionRoute(url: string | undefined): boolean {
  if (!url) return false
  // Relative to the instance baseURL, so `/portal/me` arrives as-is.
  // An absolute URL is normalised to its path first.
  const path = url.startsWith('http') ? new URL(url).pathname.replace(/^\/v1/, '') : url
  return SESSION_ROUTES.test(path)
}

/**
 * `/login`, carrying where the person was.
 *
 * A session that expires on `/authorize?client_id=…` or
 * `/cli-auth?port=…` is in the middle of handing a person to something
 * else, and that request lives only in this URL. Sending them to bare
 * `/login` dropped it: they signed in again and landed on the dashboard,
 * with the service or the CLI still waiting. `LoginPage` filters the
 * value through `safeReturnTo`, so this can only ever resume a path on
 * this origin.
 */
function loginReturningHere(): string {
  const here = `${window.location.pathname}${window.location.search}`
  return here === '/' ? '/login' : `/login?returnTo=${encodeURIComponent(here)}`
}

/**
 * Whether a request went out with the token stored now.
 *
 * A request sent with the old token can come back 401 after a new
 * sign-in has stored a fresh one — the old session expired while the
 * person was signing in again, or one tab signed in while another's
 * request was in flight. That 401 is about the old token; clearing the
 * stored one for it signed a person out moments after they signed in.
 */
function sentWithTheCurrentToken(config: { headers?: unknown } | undefined): boolean {
  const headers = config?.headers as Record<string, unknown> | undefined
  const sent = headers?.Authorization ?? headers?.authorization
  return sent === `Bearer ${localStorage.getItem(TOKEN_KEY)}`
}

client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // A session can only be *over* if we thought we had one.
      //
      // Without this, an anonymous visitor could not reach the public
      // site at all. `AuthProvider` asks `/portal/me` once when there
      // is no stored token — that is how a deployment authenticating by
      // cookie or PORTAL_AUTH_BYPASS gets recognised — and on an
      // ordinary deployment the answer is 401. This handler read that
      // as an expired login and sent the visitor to `/login`, from the
      // landing page, from `/signup`, from `/docs`. "Create an account"
      // led straight back to the sign-in form.
      //
      // It was invisible because every local stack runs the bypass and
      // answers 200, so the 401 branch never ran here. F133, and the
      // same blind spot as F130 one layer down.
      const believedSignedIn = !!localStorage.getItem(TOKEN_KEY)
      if (isSessionRoute(error.config?.url) && believedSignedIn && sentWithTheCurrentToken(error.config)) {
        localStorage.removeItem(TOKEN_KEY)
        // Skip the redirect if we're already on /login — otherwise the sign-in
        // form's own onError handler never gets to surface the error banner.
        if (window.location.pathname !== '/login') {
          window.location.href = loginReturningHere()
        }
      } else if (!isSessionRoute(error.config?.url)) {
        // Loud, because it is a bug in whoever made the call and it is
        // otherwise invisible: the request fails, the component renders
        // an empty state, and nothing says why.
        console.warn(
          `[api] 401 from ${error.config?.url} — that route does not use the portal session. ` +
            'The session was left alone; use the /portal equivalent.',
        )
      }
    }
    return Promise.reject(error)
  },
)

export default client
