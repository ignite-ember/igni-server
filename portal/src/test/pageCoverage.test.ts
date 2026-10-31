/**
 * Every file in `pages/` has a test that renders it.
 *
 * Not a rule that could be applied all at once: eleven of the thirty
 * had nothing when this was written. So it was built as a **ratchet**,
 * the same shape as `primitives.test.ts` — a list of what is still
 * owed, allowed to shrink and never to grow. That list is now empty,
 * which turns it into a plain rule: a new file in `pages/` fails until
 * something tests it.
 *
 * ## Why a rule rather than a list somebody greps for
 *
 * I counted this by hand twice and got it wrong twice.
 *
 * The first attempt matched a page's name anywhere in any `*.test.tsx`,
 * which counted `AccessPage`, `DocsPage` and `EventsPage` as covered
 * because `primitives.test.ts` — a CSS ratchet that renders nothing —
 * names their stylesheets. On that basis I reported that every routed
 * page had a test. It did not.
 *
 * The second attempt looked only for `__tests__/<Name>.test.tsx`, which
 * is right, but by then the number had already been stated wrongly.
 *
 * The third attempt required only a file named `<Name>.test.tsx`, and
 * over-reported the other way: `SettingsPage` and `LandingPage` are
 * genuinely exercised, by tests named after the sections inside them
 * (`SessionsSection.test.tsx`, `ProfileForm.test.tsx`). A filename
 * convention is not the property anybody cares about.
 *
 * So what counts is **imported by a test file** — which is as close to
 * "some test renders it" as a static check gets, and does not care what
 * the test is called. What it deliberately does not count is being
 * imported by another *page* that a test happens to render: seven
 * sections once reached the DOM through their parent page's test with
 * nothing asserting their own behaviour, and calling that coverage is
 * how the first count went wrong. When they got tests of their own,
 * four of those seven test files turned out to pass while asserting
 * nothing — the same class of defect one level up.
 *
 * A count nobody can hold in their head is a count to derive, and this
 * one needed the same discipline as everything else it measures.
 */
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, test } from 'vitest'

const PAGES = join(process.cwd(), 'src', 'pages')

/**
 * Files in `pages/` no test imports.
 *
 * **Empty**, and the ratchet keeps it that way: a new file in `pages/`
 * fails the first rule below until a test imports it. Every line here
 * was work outstanding rather than an exemption, which is why it could
 * reach zero — an exemption never expires.
 */
const OWED: readonly string[] = []

function pageModules(): string[] {
  return readdirSync(PAGES)
    .filter((name) => name.endsWith('.tsx'))
    .map((name) => name.replace(/\.tsx$/, ''))
    .sort()
}

/**
 * Every test file under `src`, read once — **except this one**.
 *
 * Excluding itself is not tidiness. The check below matched
 * `AccessPage` as covered on its fourth revision, and the match was
 * line 123 of this file: a string literal demonstrating what a false
 * positive looks like. A coverage rule that counts its own
 * documentation as coverage reports the work as done because it
 * described the work.
 */
function testSources(): string[] {
  const out: string[] = []
  const self = 'pageCoverage.test.ts'
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name)
      if (entry.isDirectory()) walk(full)
      else if (
        (entry.name.endsWith('.test.tsx') || entry.name.endsWith('.test.ts')) &&
        entry.name !== self
      )
        out.push(readFileSync(full, 'utf8'))
    }
  }
  walk(join(process.cwd(), 'src'))
  return out
}

const SOURCES = testSources()

/**
 * Whether some test imports this module.
 *
 * Matched on the end of an import path — `from '../GroupsPage'`,
 * `from '../../pages/GroupsPage'` — so a mention in prose or a CSS
 * ratchet naming its stylesheet does not count. That false positive is
 * exactly what made the first count wrong.
 */
function importedByATest(name: string): boolean {
  return SOURCES.some((text) => new RegExp(`/${name}'`).test(text))
}

describe('the scan finds the pages', () => {
  test('there are pages to check', () => {
    // A rule over an empty directory passes and proves nothing.
    expect(pageModules().length).toBeGreaterThan(15)
  })

  test('and tests to match them against', () => {
    expect(SOURCES.length).toBeGreaterThan(20)
  })

  test('a page known to be covered reads as covered', () => {
    // Guards the matcher itself: if `importedByATest` broke, everything
    // would read as owed and the list below would look complete.
    expect(importedByATest('GroupsPage')).toBe(true)
    // And one covered only through its sections, which is the case a
    // filename convention got wrong.
    expect(importedByATest('SettingsPage')).toBe(true)
  })

  test('a mention that is not an import does not count', () => {
    // A CSS ratchet naming `pages/Whatever.css` must not read as
    // coverage of `Whatever` — that false positive is how this was
    // first reported as complete. Written with a name no page has, so
    // that this assertion cannot itself become the match: the previous
    // version used a real page's name and made that page read as
    // covered, because this file was in the corpus it searched.
    expect(new RegExp("/NotARealPage'").test("import './pages/NotARealPage.css'")).toBe(false)
  })

  test('this file does not count as coverage of anything', () => {
    // The self-reference, pinned. Every page named in `OWED` below
    // appears in this file as a string; if the corpus ever included it
    // again, all of them would read as covered and the ratchet would
    // report the work finished because it had listed the work.
    expect(SOURCES.every((text) => !text.includes('does not count as coverage of anything'))).toBe(
      true,
    )
  })
})

describe('the ratchet', () => {
  test('nothing outside the list is missing a test', () => {
    const missing = pageModules().filter((name) => !importedByATest(name) && !OWED.includes(name))

    expect(missing, 'add a test, or add the file to OWED with a reason').toEqual([])
  })

  test('the list has not gone stale', () => {
    // An entry for a file that now has a test, or no longer exists,
    // makes the number owed look larger than it is — and a ratchet that
    // overstates its debt gets ignored like any other wrong number.
    const paidOff = OWED.filter((name) => importedByATest(name))
    const gone = OWED.filter((name) => !pageModules().includes(name))

    expect(paidOff, 'these have tests now — remove them from OWED').toEqual([])
    expect(gone, 'these no longer exist — remove them from OWED').toEqual([])
  })

  test('the debt is what it says', () => {
    // Stated as a ceiling so it can fall and not rise. The number is
    // the work left, which is the whole point of writing it down.
    const owed = pageModules().filter((name) => !importedByATest(name))

    expect(owed.length).toBeLessThanOrEqual(OWED.length)
  })
})
