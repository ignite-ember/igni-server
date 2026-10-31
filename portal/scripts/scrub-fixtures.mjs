/**
 * Scrub a recording before it is committed.
 *
 * E4. `record-fixtures.spec.ts` drives a real deployment and keeps
 * every response, which is the only way to get fixtures shaped like
 * what the server actually sends. It is also how a developer's data
 * ends up in the repository: the first recording carried an email
 * address at a domain we no longer use, an ngrok tunnel URL, two
 * internal container IPs, and a group description that said "the damn
 * contractors".
 *
 * So the recording is not the artefact — the *scrubbed* recording is,
 * and this is the step in between. It is a committed script rather
 * than a careful hand-edit so that re-recording next month is a
 * command and not an exercise in remembering what to look for.
 *
 *   node scripts/scrub-fixtures.mjs
 *
 * It walks the parsed structure rather than the file text, including
 * the JSON that each recorded `body` holds as a string. The first
 * version used regexes on the raw file and corrupted every fixture:
 * replacing inside a JSON-encoded string with a lookahead for the next
 * quote ate the escaping. A scrubber that produces unparseable output
 * is worse than none, because the fixtures still look present.
 *
 * Addresses use `.invalid` (RFC 2606) and IPs come from the
 * documentation ranges (RFC 5737): both are reserved precisely so that
 * example data cannot resolve to anything real.
 */

import { readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const DIR = join(
  dirname(fileURLToPath(import.meta.url)),
  '..',
  'tests',
  'e2e',
  'fixtures',
  'api',
)

let edits = 0

/** Applied to every string value found anywhere in the structure. */
function scrubString(value) {
  let out = value
  const rules = [
    [/[\w.+-]+@[\w.-]+\.[a-z]{2,}/gi, 'alex.chen@example.invalid'],
    [/https?:\/\/[a-z0-9-]+\.ngrok[a-z0-9.-]*/gi, 'https://igni.example.invalid'],
    [/\b(?:10|172)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/g, '198.51.100.7'],
    [/\b192\.168\.\d{1,3}\.\d{1,3}\b/g, '198.51.100.7'],
    [/igni-app-example-\d+/g, 'igni-app-example'],
    [/\bdamn\b/gi, 'shared'],
  ]
  for (const [find, replace] of rules) {
    out = out.replace(find, () => {
      edits += 1
      return replace
    })
  }
  // Long placeholder prose that reads as debris in a committed fixture.
  if (/Lorem Ipsum/i.test(out)) {
    edits += 1
    out = 'Contractors and agency staff. Narrower tool access, smaller default model.'
  }
  return out
}

function scrubValue(value, key) {
  if (typeof value === 'string') {
    // A recorded body is JSON in a string: parse it, scrub inside, and
    // put it back as a string so the replay layer stays unchanged.
    if (key === 'body' && /^\s*[[{]/.test(value)) {
      try {
        return JSON.stringify(scrubValue(JSON.parse(value), undefined), null, 1)
      } catch {
        // Not JSON after all — fall through and treat it as text.
      }
    }
    return scrubString(value)
  }
  if (Array.isArray(value)) return value.map((v) => scrubValue(v, undefined))
  if (value && typeof value === 'object') {
    const out = {}
    for (const [k, v] of Object.entries(value)) {
      if (k === 'app_id' && typeof v === 'number') {
        edits += 1
        out[k] = 1000001
        continue
      }
      out[k] = scrubValue(v, k)
    }
    return out
  }
  return value
}

const files = readdirSync(DIR).filter((f) => f.endsWith('.json'))
let touched = 0
for (const entry of files) {
  const path = join(DIR, entry)
  const before = readFileSync(path, 'utf8')
  const parsed = JSON.parse(before)
  const after = JSON.stringify(scrubValue(parsed, undefined), null, 2) + '\n'
  if (after !== before) {
    writeFileSync(path, after, 'utf8')
    touched += 1
  }
}
console.log(`scrubbed ${edits} value(s) across ${touched} of ${files.length} file(s)`)

// The check is the point, not the replacement: if anything identifying
// survives, or the output stopped parsing, say so rather than
// committing it.
const problems = []
for (const entry of files) {
  const text = readFileSync(join(DIR, entry), 'utf8')
  try {
    const outer = JSON.parse(text)
    for (const [key, response] of Object.entries(outer)) {
      if (typeof response?.body !== 'string') {
        problems.push(`${entry}: ${key} has no body string`)
      } else if (/^\s*[[{]/.test(response.body)) {
        JSON.parse(response.body)
      }
    }
  } catch (err) {
    problems.push(`${entry}: does not parse — ${err.message}`)
    continue
  }
  for (const [pattern, label] of [
    [/[\w.+-]+@(?!example\.invalid)[\w.-]+\.[a-z]{2,}/i, 'an email address'],
    [/ngrok/i, 'an ngrok URL'],
    [/\b(?:10|172|192\.168)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/, 'a private IP'],
    [/ember\.sh|ember-code|Ember Code/i, 'the retired brand'],
    [/\bdamn\b/i, 'test text'],
  ]) {
    const m = text.match(pattern)
    if (m) problems.push(`${entry}: ${label} — ${m[0]}`)
  }
}
if (problems.length) {
  console.error('\nNot fit to commit:\n  ' + problems.join('\n  '))
  process.exit(1)
}
console.log(`${files.length} fixture(s) parse and carry nothing identifying`)
