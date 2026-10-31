/**
 * The check that ships has to be the check that runs.
 *
 * `npx tsc --noEmit` resolves `tsconfig.json`, whose `files` is empty
 * and which only *references* the real projects — so it type-checks
 * **nothing** and reports success. `npm run build` runs `tsc -b`, which
 * builds the referenced projects and fails properly.
 *
 * That difference cost a deployment: a test file importing `node:fs`
 * broke `tsc -b`, the Docker build failed, compose kept serving the
 * previous image, and the portal looked unchanged while every local
 * check passed. Nothing was wrong with the code under test — the
 * verification was pointed at an empty project.
 *
 * So this asserts the shape that made the trap possible, so the next
 * person reads it here rather than discovering it the same way.
 */
import { readFileSync } from 'node:fs'
import { describe, expect, test } from 'vitest'

function json(path: string): Record<string, unknown> {
  // The tsconfigs carry comments, which is legal for TypeScript and not
  // for JSON.parse.
  const raw = readFileSync(path, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
  return JSON.parse(raw)
}

describe('The TypeScript project layout', () => {
  test('the root config only references, so tsc alone checks nothing', () => {
    const root = json('tsconfig.json') as { files?: unknown[]; references?: { path: string }[] }

    // If this ever stops being true the note above is stale, which is
    // worse than the trap: a comment that explains a hazard that has
    // moved.
    expect(root.files).toEqual([])
    expect(root.references?.length).toBeGreaterThanOrEqual(3)
  })

  test('every referenced project exists', () => {
    const root = json('tsconfig.json') as { references?: { path: string }[] }
    for (const { path } of root.references ?? []) {
      expect(() => json(path.replace(/^\.\//, ''))).not.toThrow()
    }
  })

  test('the build script type-checks the projects rather than the empty root', () => {
    const pkg = json('package.json') as { scripts?: Record<string, string> }
    const build = pkg.scripts?.build ?? ''

    expect(build).toContain('tsc -b')
    expect(build).not.toMatch(/\btsc --noEmit\b/)
  })

  test('tests are type-checked with Node in scope, and the app is not', () => {
    // The split that fixed it: a browser bundle should not have
    // `process` in scope, and a test that reads files should not have to
    // pretend it does not.
    const app = json('tsconfig.app.json') as { compilerOptions?: { types?: string[] } }
    const tests = json('tsconfig.test.json') as { compilerOptions?: { types?: string[] } }

    expect(app.compilerOptions?.types).not.toContain('node')
    expect(tests.compilerOptions?.types).toContain('node')
  })
})
