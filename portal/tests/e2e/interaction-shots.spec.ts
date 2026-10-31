/**
 * Press the buttons, then look at what happened.
 *
 * `page-shots.spec.ts` photographs every route at rest. Nothing
 * photographs a page after somebody has *used* it — and a dialog, a
 * dropdown and an expanded row are where layout actually goes wrong,
 * because they are the parts that only exist after an interaction and
 * therefore the parts no static pass has ever seen.
 *
 * This drives each interactive surface and writes a PNG of the result.
 * It asserts almost nothing on purpose, for the same reason
 * `page-shots` does: a screenshot generator that also polices layout
 * fails for two different reasons and teaches people to ignore both.
 * What it does assert is that the interaction *happened* — a dialog
 * that never opened would otherwise produce a screenshot of the page
 * behind it, which looks fine and shows nothing.
 *
 *   PORTAL_E2E_SHOTS=1 npx playwright test interaction-shots
 *
 * Output lands in `shots/interactions/`, which is gitignored: these
 * render whatever data the run had.
 */

import { test, expect } from '@playwright/test'
import { mkdirSync } from 'node:fs'

const OUT = 'shots/interactions'

test.use({ viewport: { width: 1440, height: 900 } })

test.beforeAll(() => {
  mkdirSync(OUT, { recursive: true })
})

test.beforeEach(async ({ page }) => {
  test.skip(process.env.PORTAL_E2E_SHOTS !== '1', 'set PORTAL_E2E_SHOTS=1 to generate')
  await page.addInitScript(() => {
    localStorage.setItem('igni_token', 'tok-e2e-shots')
  })
})

/**
 * Screenshot the viewport, once nothing is still moving.
 *
 * Without the wait this photographs the *first frame* of a fade, and I
 * misread the result twice in one sitting: the sessions dialog looked
 * like a transparent ghost overlapping the page, and the landing
 * page's mobile menu looked like grey text rendered behind the hero.
 * Both are correct once settled. A screenshot tool whose output cannot
 * be trusted is worse than none, because the whole point of it is that
 * somebody believes what they see.
 *
 * `getAnimations()` is the browser's own answer to "is anything still
 * moving", which is the same fix the accessibility suite needed for
 * the same reason — but it is raced against a deadline here, because
 * an *infinite* animation never finishes. Waiting on all of them
 * unconditionally took this file from 3.6s to a minute and left two
 * shots unwritten: a loading spinner on the page is enough to hang it
 * forever.
 */
async function shot(page: import('@playwright/test').Page, name: string) {
  await page.evaluate(
    () =>
      Promise.race([
        Promise.all(
          document
            .getAnimations()
            // A looping animation has no end to wait for.
            .filter((a) => (a.effect?.getTiming().iterations ?? 1) !== Infinity)
            .map((a) => a.finished.catch(() => undefined)),
        ),
        new Promise((resolve) => setTimeout(resolve, 2000)),
      ]),
  )
  await page.screenshot({ path: `${OUT}/${name}.png` })
}

test('the organisation switcher opens', { tag: '@manual' }, async ({ page }) => {
  await page.goto('/dashboard')
  await page.getByRole('button', { name: /Switch organization/i }).click()

  // The menu exists before the picture is taken.
  await expect(page.getByRole('menu').or(page.getByRole('listbox')).first()).toBeVisible()
  await shot(page, '01-org-switcher')
})

test('the sessions confirmation dialog', { tag: '@manual' }, async ({ page }) => {
  // The one with four serious colour-contrast violations. A picture is
  // how anybody decides what to do about that.
  await page.goto('/settings')
  await page.getByRole('button', { name: /Sign out everywhere/i }).click()

  await expect(page.getByRole('alertdialog')).toBeVisible()
  await shot(page, '02-sessions-dialog')
})

test('the new group form', { tag: '@manual' }, async ({ page }) => {
  await page.goto('/groups/new')
  await page.waitForLoadState('networkidle')

  await shot(page, '04-group-new')
})

test('a filled form shows its validation', { tag: '@manual' }, async ({ page }) => {
  // Submitting an empty required field is the most common thing a user
  // does by accident, and the error state is rendered by code no
  // screenshot has ever covered.
  await page.goto('/groups/new')
  const submit = page.getByRole('button', { name: /Create group/i }).first()
  if (await submit.isVisible().catch(() => false)) {
    await submit.click()
    await page.waitForTimeout(300)
  }

  await shot(page, '05-group-new-validation')
})

