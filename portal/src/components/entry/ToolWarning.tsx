import { Text } from '@primer/react'
import { closestTool } from '../../lib/agentTools'

/** The inline note under the editor naming tools igni does not know, or nothing. */
export function ToolWarning({ names, known }: { names: string[]; known: string[] }) {
  if (names.length === 0) return null
  return (
    <Text size="small" className="entry-tool-warning">
      {names
        .map((name) => {
          const guess = closestTool(name, known)
          return guess ? `${name} — did you mean ${guess}?` : name
        })
        .join(' · ')}{' '}
      {names.length === 1 ? 'is not a tool igni knows' : 'are not tools igni knows'}. This agent
      will fail when something calls it.
    </Text>
  )
}
