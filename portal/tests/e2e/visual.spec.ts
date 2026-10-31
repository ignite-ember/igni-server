/**
 * Visual regression: every page, both colour modes, against committed
 * baselines.
 *
 * E4, closing the open half of F67. What was here before: two
 * baselines of `/old` — the retired landing page — taken against a
 * live backend, committed only for darwin, and excluded in CI with a
 * `--grep-invert` that produced no skipped test. So the live landing
 * page had no baseline, and on the one platform CI runs, nothing was
 * compared at all.
 *
 * Three things have to hold before a pixel baseline means anything:
 *
 * **The data cannot move.** Every `/v1/` response is replayed from
 * `fixtures/api/`, recorded off a real deployment by
 * `record-fixtures.spec.ts` and scrubbed by `scripts/scrub-fixtures.mjs`.
 * A request with no recording fails the test rather than reaching a
 * server — a page quietly rendering an error state is a baseline of the
 * error state.
 *
 * **The clock cannot move.** Sixteen places render Primer's
 * `RelativeTime`, so "4 days ago" means something different tomorrow
 * and every baseline would rot on a timer. `page.clock` pins now to a
 * fixed instant just after the recorded data.
 *
 * **The platform has to have a baseline.** Playwright names them
 * per-OS. Darwin ones come from a developer's machine and Linux ones
 * from the Playwright container — see `docs/VISUAL_BASELINES.md`. A
 * test with no baseline for its platform skips itself, declared
 * `@needs-baseline`, so the reporter says so on every run instead of a
 * command-line filter hiding it.
 */

import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect, type Page } from '@playwright/test'
import { VISUAL_ROUTES } from './visual-routes'

const HERE = dirname(fileURLToPath(import.meta.url))
const FIXTURES = join(HERE, 'fixtures', 'api')
const SNAPSHOTS = join(HERE, 'visual.spec.ts-snapshots')

/**
 * Just after the newest recorded timestamp. Fixed, so `RelativeTime`
 * renders the same words forever, and late enough that nothing in the
 * data is in the future — a negative age renders differently again.
 */
const NOW = new Date('2026-09-03T12:00:00Z')

/** The baseline Playwright will look for on this platform. */
function baselineFor(name: string): string {
  return `${name}-chromium-${process.platform}.png`
}

type Recording = Record<string, { status: number; body: string }>

function recordingFor(name: string): Recording {
  const path = join(FIXTURES, `${name}.json`)
  if (!existsSync(path)) return {}
  return JSON.parse(readFileSync(path, 'utf8')) as Recording
}

/**
 * Answer every API call from the recording, and fail on anything it
 * does not cover.
 *
 * Falling back to the network would make the run depend on a backend
 * again; falling back to an empty object would silently baseline an
 * empty page. Both were considered and both hide exactly the change a
 * pixel test exists to catch.
 */
async function replay(
  page: Page,
  route: (typeof VISUAL_ROUTES)[number],
  unmatched: string[],
) {
  const name = route.name
  const recording = recordingFor(name)
  await page.route('**/v1/**', async (apiRoute) => {
    const url = new URL(apiRoute.request().url())
    const method = apiRoute.request().method()
    const withQuery = `${method} ${url.pathname}${url.search}`
    const withoutQuery = `${method} ${url.pathname}`
    const hit =
      recording[withQuery] ??
      recording[withoutQuery] ??
      // A page may paginate with a different offset than the recording
      // captured; same path, same shape.
      recording[
        Object.keys(recording).find((k) => k.split('?')[0] === withoutQuery) ?? ''
      ]
    if (!hit) {
      unmatched.push(`${name}: no recording for ${withQuery}`)
      return apiRoute.fulfill({ status: 599, body: '{"detail":"no recording"}' })
    }
    return apiRoute.fulfill({
      status: hit.status,
      contentType: 'application/json',
      body: hit.body,
    })
  })
}

for (const mode of ['light', 'dark'] as const) {
  test.describe(`visual — ${mode}`, () => {
    test.use({ colorScheme: mode, viewport: { width: 1440, height: 900 } })

    for (const route of VISUAL_ROUTES) {
      const snapshot = `${route.name}-${mode}`

      test(`${route.path} @ ${mode}`, { tag: '@needs-baseline' }, async ({
        page,
      }) => {
        // Skipping a missing baseline is right when comparing and
        // wrong when generating — the first version of this gate
        // refused to let `--update-snapshots` create the very file it
        // was demanding, and reported 32 skips as success.
        const baseline = baselineFor(snapshot)
        const generating = test.info().config.updateSnapshots !== 'none'
        test.skip(
          !generating && !existsSync(join(SNAPSHOTS, baseline)),
          `no baseline for this platform: ${baseline} — see ` +
            `docs/VISUAL_BASELINES.md`,
        )

        await page.clock.install({ time: NOW })
        const unmatched: string[] = []
        await replay(page, route, unmatched)
        if (route.signedIn) {
          await page.addInitScript(() => {
            localStorage.setItem('igni_token', 'tok-e2e-visual')
          })
        }

        await page.goto(route.path)
        await page.waitForLoadState('networkidle')
        // Skeletons resolve and web fonts settle; without this a
        // little over half the baselines picture a loading state.
        await page.waitForTimeout(700)

        expect(unmatched, unmatched.join('\n')).toEqual([])
        // The page it asked for, not wherever the router sent it.
        expect(new URL(page.url()).pathname).toBe(route.path)

        await expect(page).toHaveScreenshot(`${snapshot}.png`, {
          fullPage: true,
          // Measured rather than guessed. Two full runs at
          // `maxDiffPixels: 0` came out pixel-identical, so there is no
          // run-to-run noise to absorb on one machine; a font-size
          // change on four short numbers moved 4,985 pixels. A hundred
          // is well under any real change and above a stray antialiased
          // glyph on a different machine of the same platform.
          //
          // The first threshold here was `maxDiffPixelRatio: 0.002` —
          // about 2,600 pixels — chosen by feel, and loose enough to
          // have hidden small type changes.
          maxDiffPixels: 100,
          animations: 'disabled',
          caret: 'hide',
        })
      })
    }
  })
}
