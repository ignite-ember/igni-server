/**
 * Every page names itself, once.
 *
 * F131. Ten of ten authenticated portal pages had **no ``h1`` at all**.
 * `PageHeader.Title` was rendered `as="h2"` in all thirty places it
 * appears, and the first heading a screen reader met was the sidebar's
 * `h3` — so the document outline had no title and started three levels
 * down. Every page. The unit suite could not see it: jsdom renders the
 * markup but nothing in it asked this question, and the question is
 * about the whole page rather than a component.
 *
 * This is the guard, and it is a rule over the routes rather than a
 * list of fixed pages: a new page with no title, or a second `h1`
 * added later, fails here.
 */

import { test, expect } from '@playwright/test'

/**
 * Signed in, and told so by us rather than by whatever stack happens to
 * be running.
 *
 * Without this the guard was worthless on a machine with no backend:
 * ``/portal/me`` fails, the provider concludes signed out, every route
 * redirects to ``/login``, and the test would count that one page's
 * headings fourteen times and pass. Data requests are left to fail —
 * a page's title must not depend on its data arriving, and if one does
 * that is the finding.
 */
async function signedIn(page: import('@playwright/test').Page) {
  await page.addInitScript(() => {
    localStorage.setItem('igni_token', 'tok-e2e-headings')
  })
}

/**
 * Every route a signed-in user can reach that renders a page of its
 * own. Collection routes only — the detail pages need seeded ids, and
 * `PageHeader.Title` is shared with them.
 */
// Not `/`: since the public site moved out it only redirects to
// `/dashboard`, which is here (`portal-routes.test.tsx` covers the redirect).
const ROUTES = [
  '/dashboard',
  '/team',
  '/groups',
  '/groups/new',
  '/compliance',
  '/inference',
  '/settings',
  '/welcome',
]

test.describe('page titles', () => {
  test('every page has exactly one h1, and it is not empty', async ({
    page,
  }) => {
    // Asserted rather than assumed: a typo in the list above would
    // otherwise make this pass by checking nothing.
    expect(ROUTES.length).toBeGreaterThanOrEqual(8)

    await signedIn(page)

    const problems: string[] = []
    for (const route of ROUTES) {
      await page.goto(route)
      await page.waitForLoadState('networkidle')

      // The guard measures the page it asked for. A redirect means we
      // are looking at ``/login`` — count its headings and the result
      // is meaningless, which is how this would have passed on a
      // machine with no backend.
      const landed = new URL(page.url()).pathname
      if (landed !== route) {
        problems.push(`${route}: redirected to ${landed}, nothing measured`)
        continue
      }
      const h1s = await page
        .locator('h1')
        .evaluateAll((els) => els.map((e) => (e.textContent ?? '').trim()))

      if (h1s.length !== 1) {
        problems.push(`${route}: ${h1s.length} h1 elements (${JSON.stringify(h1s)})`)
        continue
      }
      if (!h1s[0]) problems.push(`${route}: its h1 is empty`)
    }

    expect(problems, problems.join('\n')).toEqual([])
  })
})
