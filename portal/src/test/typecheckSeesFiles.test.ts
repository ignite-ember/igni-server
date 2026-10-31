/**
 * `npx tsc --noEmit` checks nothing in this project.
 *
 * `tsconfig.json` is a solution file: `"files": []` plus three
 * references. That is the standard Vite layout and it is correct — but
 * it means a bare `tsc` compiles **zero files** and exits 0. Running
 * `tsc --noEmit --listFiles` prints nothing at all.
 *
 * This was already known. `tsconfig.test.json` carries a comment
 * saying it outright — "`npx tsc --noEmit` … resolves `tsconfig.json`,
 * whose `files` is empty, so it checked nothing and reported success"
 * — written when the test project was split out. I read that file only
 * after making the same mistake again, which is the argument for this
 * being a script and a rule rather than a third paragraph of prose: a
 * comment warns whoever opens the file, and nobody opens the file.
 *
 * I reported "tsc clean" three times on the strength of that command
 * while two of my own test files had type errors in them. The errors
 * surfaced when the portal container was built, because the Dockerfile
 * runs `npm run build`, which is `tsc -b` — build mode, which walks the
 * references. CI builds the same image, so nothing broken was ever
 * going to ship; the failure was that a local check reported success
 * without looking.
 *
 * The rule below is the cheap half of the fix: if the root config
 * compiles nothing itself, a script has to exist that does, and it has
 * to use build mode. `npm run typecheck` is now that script, so nobody
 * has to know why `tsc --noEmit` is a no-op — they just have to find a
 * command, and the one they find is the working one.
 *
 * Config-level rather than by shelling out to `tsc`: running the real
 * compiler here would take eight seconds and turn the fast suite into
 * a slow one, and the property that failed was never "does the code
 * typecheck" — it was "does the command look at anything".
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, test } from 'vitest'

const ROOT = process.cwd()

function json(name: string): Record<string, unknown> {
  // These are `tsconfig`s, and every one of them carries block
  // comments explaining itself — including the one that documented
  // this very trap. Stripping only `//` was enough to make two of the
  // rules below throw on `JSON.parse` and report as ordinary failures.
  const raw = readFileSync(join(ROOT, name), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
  return JSON.parse(raw) as Record<string, unknown>
}

const TSCONFIG = json('tsconfig.json')
const PKG = json('package.json')
const SCRIPTS = (PKG.scripts ?? {}) as Record<string, string>

/** Whether a config names any source of its own to compile. */
function compilesAnything(config: Record<string, unknown>): boolean {
  const files = config.files as unknown[] | undefined
  const include = config.include as unknown[] | undefined
  return (files?.length ?? 0) > 0 || (include?.length ?? 0) > 0
}

describe('the root config is a solution file', () => {
  test('it compiles nothing on its own', () => {
    // The premise. If this ever stops being true — someone adds an
    // `include` — then a bare `tsc` starts working and the rest of
    // this file is about a problem that no longer exists. Failing here
    // is the signal to delete it, not to work around it.
    expect(compilesAnything(TSCONFIG)).toBe(false)
    expect((TSCONFIG.references as unknown[] | undefined)?.length ?? 0).toBeGreaterThan(0)
  })

  test('the referenced projects do compile something', () => {
    // Guards the check above from being vacuously satisfied by a
    // project that has been emptied: `files: []` everywhere would also
    // pass, and would mean the build checks nothing either.
    const refs = (TSCONFIG.references as { path: string }[]).map((r) => r.path.replace('./', ''))
    expect(refs.length).toBeGreaterThan(1)

    const empty = refs.filter((path) => !compilesAnything(json(path)))
    expect(empty, 'these referenced projects name no sources').toEqual([])
  })

  test('the test files are among what gets checked', () => {
    // The errors that reached the container build were in `__tests__`.
    // Vitest does not typecheck, so if no project included the tests,
    // nothing anywhere would.
    const refs = (TSCONFIG.references as { path: string }[]).map((r) => r.path.replace('./', ''))
    const patterns = refs.flatMap((path) => (json(path).include as string[] | undefined) ?? [])

    expect(patterns.some((p) => /test/i.test(p) || p === 'src' || p.startsWith('src/**'))).toBe(true)
  })
})

describe('there is a command that actually checks', () => {
  test('a typecheck script exists', () => {
    expect(
      SCRIPTS.typecheck,
      'add `"typecheck": "tsc -b"` — `tsc --noEmit` compiles nothing here',
    ).toBeDefined()
  })

  test('it uses build mode, which is the part that matters', () => {
    // `tsc --noEmit` against a solution file is the exact command that
    // reported success while two files were broken. `-b` is what walks
    // the references.
    expect(SCRIPTS.typecheck).toMatch(/\btsc\b.*(-b|--build)/)
  })

  test('the build does too, since that is what the image runs', () => {
    // The Dockerfile's `npm run build` is the check that caught this.
    // If it ever stops typechecking, the container becomes the place
    // where type errors are discovered by not being discovered.
    expect(SCRIPTS.build).toMatch(/\btsc\b.*(-b|--build)/)
  })
})
