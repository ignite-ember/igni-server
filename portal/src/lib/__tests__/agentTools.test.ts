/**
 * Catching a mistyped tool while somebody is still typing it.
 *
 * The server says the same thing on save; this is the earlier warning.
 * Two properties matter more than the parsing: it must say nothing
 * before the real tool list has loaded — warning that every tool is
 * unknown because a fetch has not landed is worse than silence — and it
 * must never call an MCP name a typo.
 */
import { describe, expect, it } from 'vitest'
import { closestTool, declaredTools, unknownTools } from '../agentTools'

const KNOWN = ['Bash', 'Edit', 'Glob', 'Grep', 'Read', 'WebFetch', 'WebSearch', 'Write']

const agent = (tools: string) => `---\nname: reviewer\ndescription: d\ntools: ${tools}\n---\n\nBody.\n`

describe('reading the frontmatter', () => {
  it('takes a comma-joined list', () => {
    expect(declaredTools(agent('WebSearch, Bash'))).toEqual(['WebSearch', 'Bash'])
  })

  it('takes a bracketed list', () => {
    expect(declaredTools(agent('[WebSearch, Bash]'))).toEqual(['WebSearch', 'Bash'])
  })

  it('takes a YAML block list', () => {
    const content = '---\nname: r\ntools:\n  - WebSearch\n  - Bash\n---\n\nBody.\n'
    expect(declaredTools(content)).toEqual(['WebSearch', 'Bash'])
  })

  it('strips quotes', () => {
    expect(declaredTools(agent('"WebSearch", \'Bash\''))).toEqual(['WebSearch', 'Bash'])
  })

  it('says nothing for an agent that declares none', () => {
    expect(declaredTools('---\nname: r\n---\n\nBody.\n')).toEqual([])
  })

  it('says nothing for content with no frontmatter', () => {
    expect(declaredTools('just a body')).toEqual([])
  })

  it('does not mistake a later key for a list item', () => {
    const content = '---\nname: r\ntools:\n  - Bash\ncolor: cyan\n---\n\nBody.\n'
    expect(declaredTools(content)).toEqual(['Bash'])
  })
})

describe('spotting the ones that do not exist', () => {
  it('finds a typo', () => {
    expect(unknownTools(agent('WebSarch, Bash'), KNOWN)).toEqual(['WebSarch'])
  })

  it('leaves real tools alone', () => {
    expect(unknownTools(agent('WebSearch, Bash'), KNOWN)).toEqual([])
  })

  it('never calls an MCP name a typo', () => {
    // Those resolve at runtime from whatever servers the group ships.
    expect(unknownTools(agent('MCP:github, Bash'), KNOWN)).toEqual([])
  })

  it('says nothing until the real list has loaded', () => {
    // Otherwise every tool looks unknown for the first few hundred ms.
    expect(unknownTools(agent('WebSearch, Bash'), [])).toEqual([])
  })
})

describe('guessing what was meant', () => {
  it('finds the obvious near-miss', () => {
    expect(closestTool('WebSarch', KNOWN)).toBe('WebSearch')
    expect(closestTool('Bsh', KNOWN)).toBe('Bash')
  })

  it('offers nothing for something unlike any tool', () => {
    expect(closestTool('Frobnicate', KNOWN)).toBeNull()
  })
})
