/**
 * The portal cannot offer to put a customer in touch with a vendor.
 *
 * F132. Settings said *"Contact support if you need to update your
 * address"* and a failed-event banner said *"Please contact support for
 * assistance"*. On a deployment the customer runs, in their own cloud,
 * there is nobody at the other end of either sentence — the operator
 * reading it **is** the support. It is the same mistake as a hardcoded
 * vendor updater endpoint (DEC-11) or a CLI `api_url` defaulted to a
 * vendor host (DP-5a), written in prose instead of configuration.
 *
 * A rule rather than two corrections, because the next person writing
 * an error message will reach for the same phrase. Copy that has to
 * tell someone their options should name something they can actually
 * do: read the audit trail, look at the delivery attempts, ask whoever
 * administers the deployment.
 */

import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', '..')

/** Phrases that imply a vendor the customer can write to. */
const VENDOR_PHRASES = [
  /contact\s+support/i,
  /contact\s+us\b/i,
  /support\s+team/i,
  /email\s+us\b/i,
  /reach\s+out\s+to\s+us\b/i,
  /customer\s+support/i,
  /our\s+support\b/i,
  /we'?ll\s+(?:help|get\s+back)/i,
  /raise\s+a\s+ticket\s+with\s+us/i,
]

function sourceFiles(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry === '__tests__' || entry === 'test') continue
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) sourceFiles(path, found)
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) found.push(path)
  }
  return found
}

describe('self-hosted copy', () => {
  const files = sourceFiles(SRC)

  it('found the source to scan', () => {
    // A rule that reads nothing passes — F121.
    expect(files.length).toBeGreaterThan(40)
    expect(files.some((f) => f.endsWith('SettingsPage.tsx'))).toBe(true)
  })

  it('nothing offers to contact a vendor', () => {
    const offenders: string[] = []
    for (const file of files) {
      const source = readFileSync(file, 'utf8')
      source.split('\n').forEach((line, i) => {
        for (const phrase of VENDOR_PHRASES) {
          if (phrase.test(line)) {
            offenders.push(`${file.slice(SRC.length + 1)}:${i + 1} ${line.trim()}`)
          }
        }
      })
    }
    expect(
      offenders,
      [
        'This deployment runs in the customer\'s own cloud and there is no',
        'vendor to contact. Say what the person reading it can do instead —',
        'F132. If a phrase here is a false positive, narrow the pattern',
        'rather than exempting the file.',
        '',
        ...offenders,
      ].join('\n'),
    ).toEqual([])
  })
})
