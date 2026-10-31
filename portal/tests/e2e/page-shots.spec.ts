/**
 * A picture of every page, in both colour modes, for looking at.
 *
 * E2. The portal is 287 unit tests and 29 browser tests deep and not
 * one of them has ever *looked* at a page. jsdom cannot: it has no
 * layout, which is how the theme experiment came to look like it
 * worked while doing nothing (F119). This writes a PNG per route per
 * mode so the layout can be reviewed, and so E3 has something concrete
 * to fix.
 *
 * The portal renders `<ThemeProvider colorMode="auto">`, so light and
 * dark come from the browser's `prefers-color-scheme` rather than from
 * any control in the app — which is why this drives Playwright's
 * `colorScheme` instead of clicking something.
 *
 * Asserts nothing beyond "the page rendered its own title", on purpose.
 * A screenshot generator that also polices layout would fail for two
 * different reasons and teach people to ignore both. Baselines and
 * pixel comparison are E4's job; this is the pass that decides what the
 * baselines should even be.
 *
 *   PORTAL_E2E_SHOTS=1 npx playwright test page-shots
 *
 * Output lands in `shots/<mode>/`, which is gitignored: these render
 * whatever data the run had, so they are evidence of a review rather
 * than an artefact to keep.
 */

import { test, expect } from '@playwright/test'
import { mkdirSync } from 'node:fs'

import { VISUAL_ROUTES as ROUTES } from './visual-routes'

for (const mode of ['light', 'dark'] as const) {
  test.describe(`${mode} mode`, () => {
    test.use({ colorScheme: mode, viewport: { width: 1440, height: 900 } })

    test(`every page, ${mode}`, { tag: '@manual' }, async ({ page }) => {
      test.skip(
        process.env.PORTAL_E2E_SHOTS !== '1',
        'Set PORTAL_E2E_SHOTS=1 to write screenshots. This produces files ' +
          'for a human to review and asserts almost nothing, so it is not ' +
          'a gate.',
      )

      const dir = `shots/${mode}`
      mkdirSync(dir, { recursive: true })

      const missingTitle: string[] = []
      for (const route of ROUTES) {
        if (route.signedIn) {
          await page.addInitScript(() => {
            localStorage.setItem('igni_token', 'tok-e2e-shots')
          })
        }
        await page.goto(route.path)
        await page.waitForLoadState('networkidle')
        // Let skeletons resolve and Primer's fonts settle, or half the
        // shots picture a loading state.
        await page.waitForTimeout(600)

        // The one thing worth asserting: that this is the page we asked
        // for. Otherwise a redirect to /login would give sixteen
        // identical screenshots and a clean run.
        const landed = new URL(page.url()).pathname
        if (landed !== route.path) {
          missingTitle.push(`${route.path}: redirected to ${landed}`)
          continue
        }
        if ((await page.locator('h1').count()) !== 1) {
          missingTitle.push(`${route.path}: not one h1`)
        }

        await page.screenshot({
          path: `${dir}/${route.name}.png`,
          fullPage: true,
        })
        await page.screenshot({
          path: `${dir}/${route.name}-fold.png`,
          fullPage: false,
        })
      }

      expect(missingTitle, missingTitle.join('\n')).toEqual([])
    })
  })
}
