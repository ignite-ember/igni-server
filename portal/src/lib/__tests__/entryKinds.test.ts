/**
 * The kinds a group can ship.
 *
 * This table is the portal's half of a contract with two other places:
 * app/services/portal/entry_kinds.py, which enforces it, and
 * igni's loaders, which read what it produces. A kind that drifts
 * out of step here offers an admin something the server rejects, or
 * ships a file into a directory nothing scans.
 */
import { describe, expect, it } from 'vitest'
import { CONTENT_EXTENSION, EDITOR_LANGUAGE, ENTRY_KINDS, KIND_BY_ID, KIND_ORDER } from '../entryKinds'

/**
 * What the server's entry_kinds.py holds. Keep the two in step.
 *
 * Hand-maintained, which is the weakness this test cannot cover: it
 * catches the portal drifting from *this list*, not this list drifting
 * from the server. It went stale exactly that way — `scripts` was added
 * server-side and here nothing complained, because `portal/src/lib/`
 * was swallowed by a bare `lib/` in .gitignore and these tests were not
 * in the repo at all. Verify against entry_kinds.py when editing.
 */
const SERVER_KINDS: Record<string, string> = {
  agents: 'markdown',
  skills: 'markdown',
  commands: 'markdown',
  workflows: 'javascript',
  rules: 'markdown',
  'output-styles': 'markdown',
  hooks: 'json',
  tools: 'python',
  mcps: 'json',
  plugins: 'yaml',
  scripts: 'shell',
  settings: 'json',
}

describe('the kind table', () => {
  it('matches the server, kind for kind', () => {
    expect(new Set(KIND_ORDER)).toEqual(new Set(Object.keys(SERVER_KINDS)))
  })

  it('matches the server on content type', () => {
    for (const kind of ENTRY_KINDS) {
      expect(kind.contentType, kind.id).toBe(SERVER_KINDS[kind.id])
    }
  })

  it('has no duplicate ids', () => {
    expect(Object.keys(KIND_BY_ID)).toHaveLength(ENTRY_KINDS.length)
  })
})

describe('what each kind offers the admin', () => {
  it('every kind starts from something, not an empty box', () => {
    for (const kind of ENTRY_KINDS) {
      expect(kind.template.trim().length, kind.id).toBeGreaterThan(0)
    }
  })

  it('every kind says where its content lands', () => {
    // Without it, "New entry" is an act of faith.
    for (const kind of ENTRY_KINDS) {
      expect(kind.path.trim().length, kind.id).toBeGreaterThan(0)
    }
  })

  it('every content type has an editor language and an extension', () => {
    for (const kind of ENTRY_KINDS) {
      expect(EDITOR_LANGUAGE[kind.contentType], kind.id).toBeTruthy()
      expect(CONTENT_EXTENSION[kind.contentType], kind.id).toBeTruthy()
    }
  })

  it('a workflow starts from an ES module with a meta export', () => {
    // It is run by Node; a markdown scaffold would fail at import.
    expect(KIND_BY_ID.workflows.template).toContain('export const meta')
    expect(CONTENT_EXTENSION[KIND_BY_ID.workflows.contentType]).toBe('.mjs')
  })

  it('a skill scaffold carries the frontmatter its parser needs', () => {
    expect(KIND_BY_ID.skills.template).toContain('name:')
    expect(KIND_BY_ID.skills.template).toContain('description:')
  })

  it('a hook scaffold names the event it fires on', () => {
    // A declaration without one is dropped by the cache.
    expect(JSON.parse(KIND_BY_ID.hooks.template).event).toBeTruthy()
  })

  it('a hook says where it runs, and that --strict does not stop it', () => {
    // F39. igni runs a hook with `bash -c` on every member's machine, on
    // a path no permission gates. The admin writing one is the person
    // who has to know that, and the picker is where they are looking.
    const said = KIND_BY_ID.hooks.description
    expect(said).toContain('every member’s machine')
    expect(said).toContain('outside the agent’s permissions')
    expect(said).toContain('--strict')
  })
})
