#!/usr/bin/env node
/**
 * The built portal names no CDN.
 *
 * An air-gapped deployment has no route to cdn.jsdelivr.net, and a
 * browser that is told to fetch a script from it waits, fails, and
 * leaves a blank widget — which is what the content editor did, because
 * `@monaco-editor/react` loads Monaco from jsdelivr unless told otherwise.
 * The source scan in igni__server's `tests/test_nothing_phones_home.py`
 * cannot see that: the URL lives in a dependency, and only reaches the
 * product when Vite bundles it. So this reads what ships — `dist/` after
 * `npm run build` — and fails on any CDN host in it.
 *
 * Hosts, not paths: a CDN host anywhere in a shipped file is either a
 * request waiting to happen or a default somebody can fall back to.
 *
 *   node scripts/check-no-cdn.mjs [dist-dir]
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

export const CDN_HOSTS = [
  'cdn.jsdelivr.net',
  'fastly.jsdelivr.net',
  'unpkg.com',
  'cdnjs.cloudflare.com',
  'esm.sh',
  'cdn.skypack.dev',
  'ga.jspm.io',
  'jspm.dev',
  'ajax.googleapis.com',
  'fonts.googleapis.com',
  'fonts.gstatic.com',
  'code.jquery.com',
  'stackpath.bootstrapcdn.com',
  'maxcdn.bootstrapcdn.com',
  'cdn.tailwindcss.com',
  'rawgit.com',
  'raw.githubusercontent.com',
]

const SHIPPED = /\.(m?js|css|html|json|svg|webmanifest|map)$/

function* files(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) yield* files(path)
    else if (SHIPPED.test(name)) yield path
  }
}

/** [{ file, host }] for every CDN host named under `dist`. */
export function findCdnReferences(dist) {
  const found = []
  for (const path of files(dist)) {
    const text = readFileSync(path, 'utf8')
    for (const host of CDN_HOSTS) {
      if (text.includes(host)) found.push({ file: relative(dist, path), host })
    }
  }
  return found
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const dist = process.argv[2] ?? fileURLToPath(new URL('../dist', import.meta.url))
  const scanned = [...files(dist)]
  // A scan of nothing passes everything; an unbuilt tree is not a pass.
  if (!scanned.some((f) => f.endsWith('.js'))) {
    console.error(`no JavaScript under ${dist} — run \`npm run build\` first`)
    process.exit(2)
  }
  const found = findCdnReferences(dist)
  if (found.length) {
    console.error('the built portal names a CDN; an air-gapped install cannot reach it:')
    for (const { file, host } of found) console.error(`  ${host}  in ${file}`)
    process.exit(1)
  }
  console.log(`no CDN host in ${scanned.length} shipped files`)
}
