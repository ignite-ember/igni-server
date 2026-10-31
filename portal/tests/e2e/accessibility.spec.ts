/**
 * Every page, checked by axe.
 *
 * E6, closing F68. `@axe-core/playwright` was a declared dependency
 * that nothing invoked — the only "axe" in the tree was a CSS comment
 * about chart axes — so the portal carried an accessibility-testing
 * package that tested nothing, the same shape as `bandit` before F42.
 *
 * The first run found **200 violation nodes** across six rules. They
 * are all fixed (F137); this is the gate that keeps them fixed.
 *
 * Data comes from the same recorded fixtures the visual baselines use,
 * because a rule about contrast and labels has to run against a
 * populated page — an empty table has no header to be missing.
 */

import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { VISUAL_ROUTES } from './visual-routes'

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'api')

/**
 * WCAG 2.0/2.1 A and AA — what the compliance tracker's accessibility
 * claim would rest on — plus axe's best-practice set, which is where
 * `region`, `landmark-one-main` and `page-has-heading-one` live. Those
 * three were 135 of the original 200 and they are the difference
 * between a page a screen-reader user can navigate and one they cannot.
 */
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']

/**
 * Wait until an element has finished animating.
 *
 * `toBeVisible()` resolves the instant a node is painted, which for a
 * dialog is the *first frame of its fade*. Axe then measures blended,
 * mid-transition colours and reports `color-contrast` against a dialog
 * that is correct once it settles — 2 nodes at 0ms, 0 nodes at 1500ms,
 * measured directly.
 *
 * `reducedMotion: 'reduce'` below removes most of it and was not
 * enough: the test still failed in a loaded parallel run after that
 * change, and passed in the next one. So the wait is on the thing
 * itself. `getAnimations()` is the browser's own answer to "is this
 * still moving", which needs no guess about how long moving takes and
 * does not get slower on a busy machine.
 */
async function settled(locator: import('@playwright/test').Locator) {
  await locator.evaluate((el) =>
    Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished.catch(() => undefined))),
  )
}

test.describe('accessibility', () => {
  /**
   * `reducedMotion: 'reduce'` is not a nicety — it is what makes this
   * suite deterministic.
   *
   * The Sessions dialog test scanned as soon as `toBeVisible()`
   * resolved, which is **during the dialog's fade-in**. Axe measured
   * the two footer buttons mid-transition, computed the blended
   * colours, and reported `color-contrast (serious)` against a dialog
   * that is correct once it settles. Reproduced directly: scanning with
   * no wait finds 2 nodes, scanning 1.5s later finds none.
   *
   * That is why the test passed in one full run and failed in the next
   * three — a race, not a regression. An accessibility gate that
   * invents violations is worse than none, because the first thing
   * anybody does with a flaky a11y failure is stop reading a11y
   * failures.
   *
   * Reduced motion rather than a sleep: it asks the browser for the
   * settled state instead of guessing how long settling takes, it is
   * what a user with that preference actually gets, and a fixed wait
   * would have to grow every time an animation does.
   */
  test.use({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' })

  for (const route of VISUAL_ROUTES) {
    test(`${route.path} has no axe violations`, async ({ page }) => {
      const path = join(FIXTURES, `${route.name}.json`)
      const recording = existsSync(path)
        ? (JSON.parse(readFileSync(path, 'utf8')) as Record<
            string,
            { status: number; body: string }
          >)
        : {}
      const unmatched: string[] = []
      await page.route('**/v1/**', (apiRoute) => {
        const url = new URL(apiRoute.request().url())
        const method = apiRoute.request().method()
        const exact = `${method} ${url.pathname}${url.search}`
        const bare = `${method} ${url.pathname}`
        const key =
          recording[exact] !== undefined
            ? exact
            : (Object.keys(recording).find((k) => k.split('?')[0] === bare) ?? '')
        const hit = recording[key]
        if (!hit) {
          unmatched.push(exact)
          return apiRoute.fulfill({ status: 599, body: '{"detail":"no recording"}' })
        }
        return apiRoute.fulfill({
          status: hit.status,
          contentType: 'application/json',
          body: hit.body,
        })
      })
      if (route.signedIn) {
        await page.addInitScript(() => {
          localStorage.setItem('igni_token', 'tok-e2e-axe')
        })
      }

      await page.goto(route.path)
      await page.waitForLoadState('networkidle')
      await page.waitForTimeout(600)

      // Both of these keep the check honest: a page that redirected or
      // rendered an error state has few elements and passes easily.
      expect(unmatched, unmatched.join('\n')).toEqual([])
      expect(new URL(page.url()).pathname).toBe(route.path)

      const results = await new AxeBuilder({ page }).withTags(TAGS).analyze()
      const summary = results.violations.map(
        (v) =>
          `${v.id} (${v.impact}, ${v.nodes.length}×): ${v.help}\n` +
          v.nodes
            .slice(0, 5)
            .map((n) => `    ${n.target.join(' ')}\n      ${n.failureSummary?.split('\n')[1] ?? ''}`)
            .join('\n'),
      )
      expect(summary, summary.join('\n\n')).toEqual([])
    })
  }

  test('the Sessions confirmation dialog is reachable and labelled', async ({ page }) => {
    // The route loop above scans pages, and a dialog only exists once
    // somebody opens it — so B5's confirmation would have been the one
    // piece of new interface the gate never looked at. Dialogs are also
    // where a11y goes wrong specifically: no accessible name, no
    // `aria-modal`, focus left behind on the page underneath.
    const recording = JSON.parse(
      readFileSync(join(FIXTURES, '14-settings.json'), 'utf8'),
    ) as Record<string, { status: number; body: string }>

    await page.route('**/v1/**', (apiRoute) => {
      const url = new URL(apiRoute.request().url())
      const bare = `${apiRoute.request().method()} ${url.pathname}`
      const key = Object.keys(recording).find((k) => k.split('?')[0] === bare) ?? ''
      const hit = recording[key]
      return apiRoute.fulfill(
        hit
          ? { status: hit.status, contentType: 'application/json', body: hit.body }
          : { status: 599, body: '{"detail":"no recording"}' },
      )
    })
    await page.addInitScript(() => {
      localStorage.setItem('igni_token', 'tok-e2e-axe')
    })

    await page.goto('/settings')
    await page.waitForLoadState('networkidle')
    await page.getByRole('button', { name: /Sign out everywhere/i }).click()

    const dialog = page.getByRole('alertdialog')
    await expect(dialog).toBeVisible()
    await settled(dialog)
    // Named, or a screen-reader user is told only that a dialog opened.
    await expect(dialog).toHaveAccessibleName(/Sign out of every device/i)

    const results = await new AxeBuilder({ page }).withTags(TAGS).analyze()
    // The dialog is the subject, so assert it was measured: axe over a
    // page whose dialog never opened reports zero and looks identical.
    expect(await dialog.count()).toBe(1)
    const summary = results.violations.map(
      (v) => `${v.id} (${v.impact}, ${v.nodes.length}×): ${v.help}`,
    )
    expect(summary, summary.join('\n')).toEqual([])
  })
})
