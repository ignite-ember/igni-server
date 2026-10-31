/**
 * Which tools an agent asks for, read out of its frontmatter.
 *
 * The server checks this too, on save. This is the same question asked
 * while somebody is still typing, so a typo is caught before it is
 * written rather than after — and the list of real tool names is
 * fetched from the server rather than kept here, because a copy in the
 * frontend would be a third place to keep in step and the first to fall
 * behind.
 *
 * A deliberately small parser rather than a YAML dependency: the shape
 * is one key, in two spellings the loader accepts, and everything it
 * cannot read it reports as "nothing declared" — a malformed agent has
 * a bigger problem than its tool names.
 */

const FRONTMATTER = /^---\s*\n([\s\S]*?)\n---/

/** Names that resolve at runtime and cannot be checked against a list. */
const RUNTIME_PREFIXES = ['MCP:', 'mcp:']

/** The tool names an agent's frontmatter declares, in order. */
export function declaredTools(content: string): string[] {
  const block = FRONTMATTER.exec(content)?.[1]
  if (!block) return []

  const lines = block.split('\n')
  const index = lines.findIndex((line) => /^tools\s*:/.test(line))
  if (index === -1) return []

  // `tools: WebSearch, Bash`
  const inline = lines[index].replace(/^tools\s*:/, '').trim()
  if (inline) {
    return inline
      .replace(/^\[|\]$/g, '')
      .split(',')
      .map((name) => name.trim().replace(/^['"]|['"]$/g, ''))
      .filter(Boolean)
  }

  // tools:
  //   - WebSearch
  //   - Bash
  const items: string[] = []
  for (const line of lines.slice(index + 1)) {
    const item = /^\s*-\s*(.+?)\s*$/.exec(line)
    if (!item) break
    items.push(item[1].replace(/^['"]|['"]$/g, ''))
  }
  return items.filter(Boolean)
}

/**
 * Tool names the deployment will not recognise.
 *
 * Answers nothing while the known list is still loading: warning that
 * every tool is unknown because a fetch has not landed would be worse
 * than saying nothing.
 */
export function unknownTools(content: string, known: string[]): string[] {
  if (known.length === 0) return []
  const catalogue = new Set(known)
  return declaredTools(content).filter(
    (name) => !catalogue.has(name) && !RUNTIME_PREFIXES.some((p) => name.startsWith(p)),
  )
}

/** The closest real tool name, when there is an obvious one. */
export function closestTool(name: string, known: string[]): string | null {
  let best: string | null = null
  let bestDistance = Infinity
  for (const candidate of known) {
    const distance = editDistance(name.toLowerCase(), candidate.toLowerCase())
    if (distance < bestDistance) {
      bestDistance = distance
      best = candidate
    }
  }
  // Two edits on a short name is already a different word, not a slip.
  return best !== null && bestDistance <= Math.max(2, Math.floor(name.length / 4)) ? best : null
}

function editDistance(a: string, b: string): number {
  const previous = Array.from({ length: b.length + 1 }, (_, i) => i)
  for (let i = 1; i <= a.length; i++) {
    let diagonal = previous[0]
    previous[0] = i
    for (let j = 1; j <= b.length; j++) {
      const current = previous[j]
      previous[j] = Math.min(
        previous[j] + 1,
        previous[j - 1] + 1,
        diagonal + (a[i - 1] === b[j - 1] ? 0 : 1),
      )
      diagonal = current
    }
  }
  return previous[b.length]
}
