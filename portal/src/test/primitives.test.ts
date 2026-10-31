/**
 * Every value in product CSS should be a Primer primitive.
 *
 * Not a rule that can be applied all at once. A sweep of the portal's
 * stylesheets found **1,172** hardcoded colours and sizes, and 823 of
 * them were in the two marketing landing pages, which carry a deliberate
 * brand palette rather than the product's design system (those now live
 * in the public site's repository). Converting the
 * rest in one change would touch every screen at once, and the portal
 * has no visual baselines to catch what moved (F67, still an open
 * decision) — so a big-bang rewrite is precisely the change that cannot
 * be verified.
 *
 * So this is a **ratchet**, not a gate. Each file carries the number of
 * literals it had when the rule was written; the count may fall and
 * never rise, and a file not listed must have none at all. New CSS is
 * therefore held to the standard immediately, and the debt can only be
 * paid down.
 *
 * A ratchet rather than an exemption list because an exemption never
 * expires. This one names a number, and the number is the work left.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, test } from 'vitest'

// vitest runs from the portal root, so this is stable whichever file
// the test lives in.
const SRC = join(process.cwd(), 'src')

/**
 * Literals that are Primer's job to decide.
 *
 * A value inside `var(--token, fallback)` is not counted: the fallback
 * is the token's own published value, written for the case where the
 * primitives stylesheet has not loaded, and is the documented way to
 * write one.
 */
const HEX = /#[0-9a-fA-F]{3,8}\b/g
const PX = /(?<![\w-])(\d+(?:\.\d+)?)px/g

/**
 * What each file still owes, as of this rule being written.
 *
 * The two landing pages were the bulk — 1,300 of the literals, a
 * marketing surface with its own visual language. They moved to the
 * public site's own repository (igni-site) on 2026-10-31 with the
 * documentation pages, and their ceilings went with them.
 */
const CEILINGS: Record<string, number> = {
  'auth/CLIAuthPage.css': 42,
  'pages/GroupsPage.css': 17,
  'components/layout/Sidebar.css': 16,
  'pages/WelcomePage.css': 6,
  // Component widths and fixed heights, kept deliberately: Primer
  // publishes no token for "how wide a filter dropdown should be", and a
  // spacing token used as a width is a token used for the wrong thing.
  'pages/TeamPage.css': 4,
  'pages/GroupDetailPage.css': 1,
  'pages/SettingsPage.css': 1,
  'components/ContentEditor.css': 1,
  'pages/DashboardPage.css': 1,
  'pages/NewGroupPage.css': 1,
  'pages/CompliancePage.css': 1,
  'auth/LoginPage.css': 2,
  'pages/cards.css': 0,
}

function stylesheets(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) stylesheets(path, found)
    else if (entry.endsWith('.css')) found.push(path)
  }
  return found
}

function literals(source: string): number {
  let count = 0
  for (const line of source.split('\n')) {
    const trimmed = line.trim()
    if (trimmed.startsWith('*') || trimmed.startsWith('/*')) continue
    const withoutFallbacks = line.replace(/var\(--[\w-]+,\s*[^)]*\)/g, 'var(--token)')
    count += (withoutFallbacks.match(HEX) ?? []).length
    count += (withoutFallbacks.match(PX) ?? []).length
  }
  return count
}

describe('Primer primitives', () => {
  test('no stylesheet has more literals than it is allowed', () => {
    const over: string[] = []
    for (const path of stylesheets(SRC)) {
      const name = path.slice(SRC.length).replace(/^\/+/, '')
      const count = literals(readFileSync(path, 'utf8'))
      const ceiling = CEILINGS[name] ?? 0
      if (count > ceiling) {
        over.push(`${name}: ${count} literal(s), allowed ${ceiling}`)
      }
    }

    expect(over, [
      'These use hardcoded colours or sizes where a Primer primitive exists.',
      'Spacing is --base-size-*, text is --text-*-size-*, colour is a functional',
      'role like --fgColor-muted, borders are --borderWidth-* and --borderRadius-*.',
      'See https://primer.style/product/primitives/.',
      '',
      'If a value genuinely has no token — a component width, a fixed height —',
      'raise the ceiling in this file with the reason.',
    ].join('\n')).toEqual([])
  })

  test('every ceiling is still needed', () => {
    // A ceiling above what a file actually contains is a debt that has
    // been paid and not written down, and it lets the next literal in
    // for free. Lower it when you lower the count.
    const slack: string[] = []
    for (const [name, ceiling] of Object.entries(CEILINGS)) {
      const path = join(SRC, name)
      let source: string
      try {
        source = readFileSync(path, 'utf8')
      } catch {
        slack.push(`${name}: listed but does not exist`)
        continue
      }
      const count = literals(source)
      if (count < ceiling) slack.push(`${name}: allowed ${ceiling}, has ${count} — lower it`)
    }

    expect(slack).toEqual([])
  })

  test('the scan reads the stylesheets', () => {
    // Every assertion above is "this list is empty", so a scan that
    // found no files would pass in silence.
    const found = stylesheets(SRC)
    expect(found.length).toBeGreaterThan(15)
    expect(literals('color: #ff0000; padding: 8px;')).toBe(2)
    expect(literals('color: var(--fgColor-muted, #656d76);')).toBe(0)
  })
})
