/**
 * F132. "1 members" and "1 people" shipped on the two pages a fresh
 * deployment shows first — the pages where the count is exactly one.
 * Two instances of one missing helper.
 *
 * The helper is tested here, and then the second test is the part that
 * matters: a rule over the source, so the *next* count rendered by
 * hand fails before anyone has to see it on a page. A runtime check
 * cannot do that job, because it only fires when a page happens to be
 * showing exactly one of something.
 */

import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { countOf, pluralise } from '../plural'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', '..')

describe('countOf', () => {
  it('says member for one and members for anything else', () => {
    expect(countOf(1, 'member')).toBe('1 member')
    expect(countOf(0, 'member')).toBe('0 members')
    expect(countOf(2, 'member')).toBe('2 members')
  })

  it('takes an irregular plural, because English has them', () => {
    expect(countOf(1, 'person', 'people')).toBe('1 person')
    expect(countOf(3, 'person', 'people')).toBe('3 people')
  })

  it('groups thousands, since these are counts a human reads', () => {
    expect(countOf(12_345, 'request').replace(/[^0-9a-z ]/gi, '')).toBe('12345 requests')
  })

  it('pluralise gives the noun alone', () => {
    expect(pluralise(1, 'node')).toBe('node')
    expect(pluralise(4, 'node')).toBe('nodes')
  })
})

function sourceFiles(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry === '__tests__' || entry === 'test') continue
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) sourceFiles(path, found)
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) found.push(path)
  }
  return found
}

describe('counts go through the helper', () => {
  const files = sourceFiles(SRC)

  it('found the source to scan', () => {
    // A scan of nothing passes, which is worse than no scan (F121).
    expect(files.length).toBeGreaterThan(40)
    expect(files.some((f) => f.endsWith('TeamPage.tsx'))).toBe(true)
  })

  it('no count reaches the page without its singular handled', () => {
    // The shape that produced "1 members": a count interpolated in text
    // position with nothing deciding the noun's form.
    //
    // Text position, not anywhere — an interpolation preceded by `=` is
    // a JSX attribute, and `value={String(nodes.length)}` is not a
    // sentence. The first attempt at this rule matched those too and
    // reported twenty offenders, of which two were real.
    //
    // A site passes by using the helper, by handling the singular
    // inline, or by saying `count-ok` on the line: "12 with access" has
    // no plural to get wrong, and a rule cannot tell that from
    // "12 members" — so the exception is declared where a reader of
    // that line will see it, rather than hidden in this file.
    const SHAPE =
      /(?:^[ \t]*|>)\{[^{}]*\b(?:length|total|count|_count)\b[^{}]*\}[ \t]*\n?[ \t]*[A-Za-z]/gm
    const offenders: string[] = []
    for (const file of files) {
      const source = readFileSync(file, 'utf8')
      const lines = source.split('\n')
      for (const m of source.matchAll(SHAPE)) {
        const lineNo = source.slice(0, m.index).split('\n').length
        const context = lines.slice(lineNo - 2, lineNo + 2).join('\n')
        const handled =
          /\b(?:countOf|pluralise)\s*\(/.test(context) ||
          /[!=]==?\s*1\b|\b1\s*[!=]==?/.test(context) ||
          /count-ok/.test(context)
        if (handled) continue
        offenders.push(
          `${file.slice(SRC.length + 1)}:${lineNo} ${JSON.stringify(m[0].replace(/\s+/g, ' '))}`,
        )
      }
    }
    expect(
      offenders,
      [
        'A count reaches the page with nothing deciding the noun that',
        "follows it. Use countOf(n, 'member') — F132 — or, if there is no",
        'plural to get wrong, write `count-ok` on the line to say so.',
        '',
        ...offenders,
      ].join('\n'),
    ).toEqual([])
  })

  it('nothing hedges with "(s)"', () => {
    // `${n} request(s)` is the same defect wearing a disguise: it is
    // never right, only never wrong.
    const offenders: string[] = []
    for (const file of files) {
      const source = readFileSync(file, 'utf8')
      for (const m of source.matchAll(/[A-Za-z]+\(s\)/g)) {
        // `http(s)://` is a scheme, not a noun.
        if (/^https?\(s\)$/i.test(m[0]) || m[0] === 'http(s)') continue
        const line = source.slice(0, m.index).split('\n').length
        offenders.push(`${file.slice(SRC.length + 1)}:${line} ${m[0]}`)
      }
    }
    expect(offenders, offenders.join('\n')).toEqual([])
  })
})
