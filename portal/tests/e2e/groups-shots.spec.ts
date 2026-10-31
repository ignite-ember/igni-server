/**
 * Screenshots of the group screens, for looking at rather than asserting.
 *
 * Not a regression suite: it writes full-page PNGs of every group surface
 * so the layout can be reviewed against Primer's guidance. Run against a
 * deployment that has a Default group and at least one ordinary one:
 *
 *   PORTAL_URL=http://localhost:3000 npx playwright test groups-shots
 */
import { test, expect } from '@playwright/test'
import { mkdirSync } from 'node:fs'

const OUT = process.env.SHOT_DIR ?? 'shots'

test.beforeAll(() => {
  mkdirSync(OUT, { recursive: true })
})

test.use({ viewport: { width: 1440, height: 900 } })

async function settle(page: import('@playwright/test').Page) {
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(400)
}

// This is a screenshot generator, not an assertion suite: it walks the
// group screens writing PNGs for documentation. It needs a live portal
// backed by a real API with a seeded Default group, which CI does not have
// — unmocked, `/groups` renders empty and the first expect fails.
//
// Gated rather than deleted: the screenshots are useful, and a spec that
// fails for want of an environment teaches people to ignore the suite.
// Set PORTAL_E2E_SHOTS=1 against a running portal to produce them.
//
// The @manual tag is what makes this skip legitimate rather than
// invisible — see tests/e2e/reporters/skips.ts. An untagged skip fails
// the run under IGNI_E2E_STRICT=1.
test('group screens', { tag: '@manual' }, async ({ page }) => {
  test.skip(
    process.env.PORTAL_E2E_SHOTS !== '1',
    'Set PORTAL_E2E_SHOTS=1 with a live portal + seeded Default group.',
  )

  await page.goto('/groups')
  await settle(page)
  await page.screenshot({ path: `${OUT}/01-groups-list.png`, fullPage: true })

  // The Default group — the read-only one.
  const defaultRow = page.getByRole('link', { name: 'Default', exact: true })
  await expect(defaultRow).toBeVisible()
  await defaultRow.click()
  await settle(page)
  await page.screenshot({ path: `${OUT}/02-default-members.png`, fullPage: true })

  await page.getByRole('link', { name: /configuration/i }).click()
  await settle(page)
  await page.screenshot({ path: `${OUT}/03-default-configuration.png`, fullPage: true })

  const firstEntry = page.locator('a[href*="/entries/"]').first()
  const entryHref = await firstEntry.getAttribute('href')
  await firstEntry.click()
  await settle(page)
  await page.screenshot({ path: `${OUT}/04-default-entry-readonly.png`, fullPage: true })
  test.info().annotations.push({ type: 'entry', description: entryHref ?? '' })

  await page.goto('/groups')
  await settle(page)
  const other = page.getByRole('link', { name: 'contractors', exact: true })
  await other.click()
  await settle(page)
  await page.getByRole('link', { name: /configuration/i }).click()
  await settle(page)
  await page.screenshot({ path: `${OUT}/05-ordinary-configuration.png`, fullPage: true })

  await page.getByRole('link', { name: /general/i }).click()
  await settle(page)
  await page.screenshot({ path: `${OUT}/06-ordinary-general.png`, fullPage: true })

  await page.goto('/groups/new')
  await settle(page)
  await page.screenshot({ path: `${OUT}/07-new-group.png`, fullPage: true })
})
