import type { AuthMethods } from './useAuthMethods'

/** A social sign-in provider this portal has a button for. */
export type OAuthProvider = 'google'

/**
 * Where the server sends a finished sign-in when nothing says otherwise,
 * so naming it again in the query string would add nothing.
 */
const SERVER_DEFAULT_RETURN_TO = '/select-org'

function withReturnTo(start: string, returnTo: string): string {
  if (returnTo === SERVER_DEFAULT_RETURN_TO) return start
  return `${start}?returnTo=${encodeURIComponent(returnTo)}`
}

/** The URL that starts a sign-in with `provider`, carrying `returnTo` through it. */
export function oauthStartUrl(provider: OAuthProvider, returnTo: string): string {
  return withReturnTo(`/v1/auth/${provider}`, returnTo)
}

/**
 * The URL that starts single sign-on. The start URL comes from the
 * server rather than being built here, so the one place that knows
 * where the flow begins is the one that implements it.
 */
export function ssoStartUrl(methods: AuthMethods, returnTo: string): string {
  return withReturnTo(methods.sso.start_url, returnTo)
}
