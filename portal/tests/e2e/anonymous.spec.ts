/**
 * The public site has to work for someone who is not signed in.
 *
 * F133. `AuthProvider` asks `/portal/me` once when there is no stored
 * token — that is how a deployment authenticating by cookie or
 * `PORTAL_AUTH_BYPASS` gets recognised. On an ordinary deployment the
 * answer is 401, and the response interceptor read that as an expired
 * session and sent the visitor to `/login`. From the landing page.
 * From `/signup`. From `/docs`. "Create an account" led back to the
 * sign-in form.
 *
 * Nothing caught it because every local stack runs the bypass and
 * answers 200, so the 401 branch never ran — the same blind spot as
 * F130, one layer down in the product rather than in a test.
 *
 * Both directions are checked here. Loosening the rule until the public
 * pages work is easy; the half that matters is that an *expired*
 * session still ends.
 */

import { test, expect } from '@playwright/test'

/** What any deployment without the bypass answers a visitor. */
async function serverSaysNotSignedIn(page: import('@playwright/test').Page) {
  await page.route('**/v1/portal/me', (route) =>
    route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: '{"detail":"Not authenticated"}',
    }),
  )
}

// `/` and `/docs` were on this list while the portal bundle was also the
// public site; the portal's `/` opens the dashboard, and the docs moved
// to their own repository.
const PUBLIC_PAGES = ['/login', '/signup']

test.describe('a visitor with no session', () => {
  test('reaches every public page instead of being bounced to /login', async ({
    page,
  }) => {
    await serverSaysNotSignedIn(page)
    for (const path of PUBLIC_PAGES) {
      await page.goto(path)
      await page.waitForLoadState('networkidle')
      // The redirect was a `window.location.href` assignment, so it
      // arrives as a fresh navigation rather than a router transition —
      // wait long enough for one.
      await page.waitForTimeout(900)
      expect(
        new URL(page.url()).pathname,
        `${path} sent an anonymous visitor to ${new URL(page.url()).pathname}`,
      ).toBe(path)
    }
  })

  test('is still sent to /login from a protected page', async ({ page }) => {
    await serverSaysNotSignedIn(page)
    await page.goto('/dashboard')
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(900)
    expect(new URL(page.url()).pathname).toBe('/login')
  })
})

test.describe('a session that has expired', () => {
  test('ends: the token is dropped and the visitor lands on /login', async ({
    page,
  }) => {
    // A token in hand and a server that rejects it. This is the case
    // the interceptor exists for, and the case a fix for F133 could
    // easily have broken — "believed signed in" is what separates the
    // two, and if it were read wrongly a revoked token would keep
    // rendering the portal shell.
    await serverSaysNotSignedIn(page)

    // Seeded once, on a page that is already open — not with
    // `addInitScript`, which re-runs on every navigation *including the
    // redirect*, so the token this test is watching for the removal of
    // was being written back immediately after it was removed. The
    // check passed on the URL and failed on the token, which is the
    // only reason the flaw showed at all.
    await page.goto('/login')
    await page.evaluate(() => localStorage.setItem('igni_token', 'expired-token'))

    await page.goto('/dashboard')
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(1_200)

    expect(new URL(page.url()).pathname).toBe('/login')
    expect(
      await page.evaluate(() => localStorage.getItem('igni_token')),
      'the rejected token was left in place',
    ).toBeNull()
  })
})
