/**
 * Where a sign-in is allowed to send you afterwards.
 *
 * `returnTo` arrives in a query string, survives an OAuth or SSO round
 * trip through the provider, and is then handed to `navigate()`. If a
 * hostile value can reach that call, a link like
 *
 *     /login?returnTo=//evil.example.com
 *
 * is an open redirect off the back of a real sign-in — and the page it
 * leaves has the freshly issued token in its own URL, so the referrer
 * carries it along.
 *
 * **React Router already refuses this.** Measured, not assumed: against
 * 7.18.3, `router.navigate()` throws `External navigation is not
 * allowed` for `//evil.example.com`, `https://evil.example.com`,
 * `/\evil.example.com` and `javascript:alert(1)`. So there is no live
 * hole today.
 *
 * This exists because that protection is *incidental* — it comes from a
 * guard inside a dependency rather than from anything this application
 * says. The same reasoning is already written down in
 * `app/services/auth/oidc.py` about pinning the JWT algorithm set:
 * naming the rule costs nothing and means a router upgrade, a downgrade
 * within the `^7` range, or a move to another router cannot quietly
 * reopen it. A thrown exception is also a worse outcome than a redirect
 * to the dashboard, which is what this produces.
 */

/** The only destination that is always safe. */
export const DEFAULT_RETURN_TO = '/dashboard'

/**
 * A same-origin path, or {@link DEFAULT_RETURN_TO}.
 *
 * Accepts exactly one shape: a single leading `/` followed by something
 * that is not another `/` or a `\`. That covers every legitimate value —
 * `/dashboard`, `/cli-auth?port=51234`, `/groups/abc#members` — and
 * excludes the protocol-relative and backslash forms browsers resolve to
 * another host.
 */
export function safeReturnTo(candidate: string | null | undefined): string {
  if (!candidate) return DEFAULT_RETURN_TO

  // Not a path at all.
  if (!candidate.startsWith('/')) return DEFAULT_RETURN_TO

  // `//host` and `/\host` are both resolved by browsers as
  // protocol-relative URLs pointing at another origin.
  if (candidate.startsWith('//') || candidate.startsWith('/\\')) return DEFAULT_RETURN_TO

  // Browsers strip leading whitespace and control characters before
  // parsing, so a tab after the slash turns `/<tab>/evil.example.com`
  // into `//evil.example.com` *after* the check above has passed it.
  // Rejecting the class beats reasoning about each character.
  //
  // Written as an explicit codepoint range. The first version was a
  // two-character class that read like a range — space and hyphen —
  // so it rejected `/cli-auth?port=...`, one of the two destinations
  // this function exists to allow. Caught by the test that lists the
  // real ones.
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u0020\u007f]/.test(candidate)) return DEFAULT_RETURN_TO

  return candidate
}
