/**
 * Record what each page asks the API for, so the visual baselines can
 * be compared against something that does not move.
 *
 * E4. A pixel baseline taken against a live backend is worthless: the
 * audit trail grows, counts change, `RelativeTime` renders "4 days
 * ago" and means something different tomorrow. The two baselines that
 * existed before this were taken against `/old`, a page with no data
 * at all, which is the only reason they held still.
 *
 * So: drive every route once against a real deployment, keep every
 * `/v1/` response, and let `visual.spec.ts` replay them. Recorded
 * rather than hand-written because hand-written fixtures drift from
 * the shapes the server actually sends, and a baseline of a page
 * rendering the wrong shape is a picture of a bug.
 *
 *   PORTAL_E2E_RECORD=1 npx playwright test record-fixtures
 *
 * The output is committed, and it is reviewed before it is: a recording
 * from a developer's machine carries their data. `scripts/` has the
 * scrubber.
 */

import { test } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { VISUAL_ROUTES } from './visual-routes'

const OUT = join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'api')

test('record every API response each page needs', { tag: '@manual' }, async ({
  page,
}) => {
  test.skip(
    process.env.PORTAL_E2E_RECORD !== '1',
    'Set PORTAL_E2E_RECORD=1 against a running stack to re-record. The ' +
      'recordings are committed; this rewrites them.',
  )
  test.setTimeout(180_000)

  mkdirSync(OUT, { recursive: true })

  // A public page has to be recorded *as a visitor*: with a token
  // seeded, `/signup` never asked `/v1/auth/methods` and its recording
  // came out empty, so the replay had nothing to answer with. The
  // routes are ordered public-first, so the session is seeded once, on
  // the way past.
  let seeded = false

  // And a visitor has to be *told* they are not signed in. The stack
  // these recordings come from runs PORTAL_AUTH_BYPASS, so it answers
  // 200 to `/portal/me` with no token; recorded as-is, the sign-in page
  // would have been captured in its signed-in state and never asked
  // `/v1/auth/methods` at all. Stating it here rather than in the
  // replay keeps one source of truth — the fixture.
  await page.route('**/v1/portal/me', async (apiRoute) => {
    if (seeded) return apiRoute.fallback()
    return apiRoute.fulfill({
      status: 401,
      contentType: 'application/json',
      body: '{"detail":"Not authenticated"}',
    })
  })

  for (const route of VISUAL_ROUTES) {
    if (route.signedIn && !seeded) {
      await page.addInitScript(() => {
        localStorage.setItem('igni_token', 'tok-e2e-record')
      })
      seeded = true
    }
    const captured: Record<string, { status: number; body: string }> = {}

    page.on('response', async (response) => {
      const url = new URL(response.url())
      if (!url.pathname.startsWith('/v1/')) return
      const key = `${response.request().method()} ${url.pathname}${url.search}`
      try {
        captured[key] = { status: response.status(), body: await response.text() }
      } catch {
        // A response whose body is already gone — a redirect, or a
        // request the page cancelled. Nothing to replay.
      }
    })

    await page.goto(route.path)
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(800)
    page.removeAllListeners('response')

    writeFileSync(
      join(OUT, `${route.name}.json`),
      JSON.stringify(captured, null, 2) + '\n',
      'utf8',
    )
    console.log(`${route.path} → ${Object.keys(captured).length} responses`)
  }
})
