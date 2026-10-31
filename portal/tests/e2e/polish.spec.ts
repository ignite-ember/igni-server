/**
 * The defects a browser can see and jsdom cannot.
 *
 * F132. Search boxes were 128px wide — Primer's `TextInput` default,
 * with no width given — while their placeholders needed up to 320px,
 * so a placeholder stopped mid-word. The sign-in footer was capped to the width of the card above it and its
 * one line of text needed 20px more, so "Forgot password?" broke in
 * half on the first page anyone sees. And the sidebar carried two rows
 * labelled "Integrations", same icon, different destinations.
 *
 * None of that is visible to a unit test: jsdom has no layout, so
 * every width it reports is zero. These are rules rather than
 * measurements of the boxes that were wrong — a search field added
 * tomorrow is checked by the same test.
 */

import { test, expect } from '@playwright/test'

const ROUTES = [
  '/dashboard',
  '/team',
  '/groups',
  '/groups/new',
  '/inference',
  '/compliance',
  '/settings',
  '/welcome',
]

test.describe('layout', () => {
  test.use({ viewport: { width: 1440, height: 900 } })

  test('no text is cut off with no way to read the rest', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('igni_token', 'tok-e2e-polish')
    })

    expect(ROUTES.length).toBeGreaterThanOrEqual(8)
    const problems: string[] = []

    for (const route of ROUTES) {
      await page.goto(route)
      await page.waitForLoadState('networkidle')
      await page.waitForTimeout(400)

      const landed = new URL(page.url()).pathname
      if (landed !== route) {
        problems.push(`${route}: redirected to ${landed}, nothing measured`)
        continue
      }

      const clipped = await page.evaluate(() => {
        const out: string[] = []
        const SKIP = new Set(['STYLE', 'SCRIPT', 'TITLE', 'NOSCRIPT'])
        for (const el of document.querySelectorAll(
          'input,button,a,span,div,td,th,p,label',
        )) {
          const e = el as HTMLElement
          if (SKIP.has(e.tagName)) continue
          if (e.children.length > 0 && e.tagName !== 'INPUT') continue
          const style = getComputedStyle(e)
          // Primer's VisuallyHidden clips to a 1px box deliberately.
          if (e.clientWidth <= 2 || e.clientHeight <= 2) continue
          if (style.position === 'absolute' && style.clip !== 'auto') continue

          if (e.tagName === 'INPUT') {
            const i = e as HTMLInputElement
            if (!i.placeholder) continue
            // Measure the placeholder against the box that has to show
            // it, in the font it is actually rendered in.
            const probe = document.createElement('span')
            probe.style.cssText = `position:absolute;visibility:hidden;white-space:nowrap;font:${style.font}`
            probe.textContent = i.placeholder
            document.body.appendChild(probe)
            const need = probe.getBoundingClientRect().width
            probe.remove()
            // A leading visual and the input's own padding eat room the
            // placeholder cannot use.
            const shows = i.clientWidth - 34
            if (need > shows + 2) {
              out.push(
                `placeholder needs ${Math.round(need)}px, box shows ` +
                  `${Math.round(shows)}px: ${JSON.stringify(i.placeholder)}`,
              )
            }
            continue
          }

          // A line-clamped or ellipsised block is a deliberate summary.
          // A single line that simply does not fit is not.
          if (style.webkitLineClamp && style.webkitLineClamp !== 'none') continue
          if (style.textOverflow === 'ellipsis') continue
          if (e.scrollWidth > e.clientWidth + 2 && (e.textContent ?? '').trim()) {
            out.push(
              `hard clip: ${JSON.stringify((e.textContent ?? '').trim().slice(0, 60))}`,
            )
          }
        }
        return [...new Set(out)]
      })

      for (const c of clipped) problems.push(`${route}: ${c}`)
    }

    expect(problems, problems.join('\n')).toEqual([])
  })

  test('the sign-in footer fits on one line', async ({ page }) => {
    await page.goto('/login')
    await page.waitForLoadState('networkidle')
    const footer = await page.evaluate(() => {
      const p = [...document.querySelectorAll('p')].find((e) =>
        /New to igni/.test(e.textContent ?? ''),
      )
      if (!p) return null
      const style = getComputedStyle(p)
      const probe = document.createElement('span')
      probe.style.cssText = `position:absolute;visibility:hidden;white-space:nowrap;font:${style.font}`
      probe.textContent = p.textContent
      document.body.appendChild(probe)
      const need = probe.getBoundingClientRect().width
      probe.remove()
      return {
        need: Math.round(need),
        box: Math.round(p.getBoundingClientRect().width),
        align: style.textAlign,
      }
    })
    expect(footer, 'the sign-in footer text was not found').not.toBeNull()
    expect(
      footer!.need,
      `"New to igni? …" needs ${footer!.need}px and its box is ${footer!.box}px, ` +
        `so it breaks mid-phrase`,
    ).toBeLessThanOrEqual(footer!.box)
    // If it ever does wrap — a longer translation, a larger font — both
    // lines should stay centred rather than ragging left under a
    // centred card.
    expect(footer!.align).toBe('center')
  })

  test('no two navigation entries carry the same label', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('igni_token', 'tok-e2e-polish')
    })
    await page.goto('/dashboard')
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(400)

    const nav = await page.evaluate(() => {
      // One entry per item. Selecting `li` would count each group's
      // wrapper too and report every label as a duplicate — which is
      // what the first version of this check did.
      const items = [...document.querySelectorAll('nav [class*="ActionListItem"]')]
      const labels = items
        .map((e) => (e.textContent ?? '').trim())
        .filter(Boolean)
      const counts: Record<string, number> = {}
      for (const l of labels) counts[l] = (counts[l] ?? 0) + 1
      return {
        count: labels.length,
        duplicates: Object.entries(counts)
          .filter(([, n]) => n > 1)
          .map(([label, n]) => `${label} ×${n}`),
      }
    })

    // The selector has to have found the nav, or the check is vacuous.
    expect(nav.count, 'no navigation items were found to check').toBeGreaterThan(8)
    expect(nav.duplicates, nav.duplicates.join(', ')).toEqual([])
  })
})
