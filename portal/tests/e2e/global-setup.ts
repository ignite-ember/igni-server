/**
 * Say what is missing before the run, not during it.
 *
 * E1, F130. This suite drives the portal against a real backend. When
 * the backend was absent, nothing said so: the specs went to a page
 * that never resolved and failed five seconds later on a URL
 * assertion, fourteen retries deep, with a screenshot of a blank div.
 * Two of them failed for the opposite reason — a backend that *was*
 * there and answered ``200`` to ``/portal/me`` because the local stack
 * runs ``PORTAL_AUTH_BYPASS=1``.
 *
 * Neither failure was about the thing being tested, and neither said
 * the word "backend". So this runs first and says what it found.
 *
 * It does not *require* one. The suite is hermetic on purpose — every
 * spec that cares about the server's answer intercepts the route and
 * states it — so a run with no backend is legitimate, and that is what
 * CI does. What is not legitimate is asking for a deployment and
 * silently getting something else, so an unreachable or broken
 * ``PORTAL_URL`` is fatal: naming a target is a claim about where the
 * run happened.
 */

import type { FullConfig } from '@playwright/test'

const PROBE = '/v1/portal/me'

export default async function globalSetup(config: FullConfig): Promise<void> {
  const baseURL =
    config.projects[0]?.use?.baseURL ??
    process.env.PORTAL_URL ??
    'http://localhost:5173'

  // Set explicitly = "run against this deployment". Unset = the local
  // dev server, where a backend is a convenience.
  const targeted = !!process.env.PORTAL_URL
  const url = new URL(PROBE, baseURL).toString()
  let status: number
  try {
    // No token. A 200 means the deployment authenticates some other
    // way; a 401 means it wants one. Both are usable — the specs say
    // which they mean. Anything else, or nothing, is not.
    const res = await fetch(url, { headers: { Accept: 'application/json' } })
    status = res.status
  } catch (err) {
    const message =
      `${url} is not answering (${String(err)}).\n` +
      `  · start the stack with \`make up\` at the repository root, or\n` +
      `  · point the suite elsewhere with PORTAL_URL=…`
    if (targeted) {
      throw new Error(
        `PORTAL_URL was set, so this run claims to test a deployment, ` +
          `and ${message}`,
      )
    }
    console.log(
      `\nno backend behind ${baseURL}. Every spec that depends on the ` +
        `server's answer mocks it, so this run is still meaningful — ` +
        `but nothing here exercised a real API.\n${message}\n`,
    )
    return
  }

  if (status !== 200 && status !== 401) {
    // 502/503/504 through the dev server's proxy means the proxy is
    // fine and the API behind it is not — worth saying, because
    // "answered 502" otherwise reads as the portal being broken.
    const gateway = status === 502 || status === 503 || status === 504
    const complaint = gateway
      ? `${url} answered ${status}: the portal is being served but the ` +
        `API behind it is not answering. Start it with \`make up\` at ` +
        `the repository root. Specs that mock the server's reply still ` +
        `mean something; nothing here will touch a real API.`
      : `${url} answered ${status}. The suite expects 200 (this ` +
        `deployment signs us in) or 401 (it wants a token); anything ` +
        `else means the stack is up but answering wrongly, and the ` +
        `specs would fail in ways that look like product bugs.`
    if (targeted) throw new Error(complaint)
    console.log(`\n${complaint}\n`)
    return
  }

  const mode =
    status === 200
      ? 'signs us in without a token (PORTAL_AUTH_BYPASS or a cookie)'
      : 'requires a token'
  console.log(`\nbackend at ${baseURL} is up and ${mode}.`)
}
