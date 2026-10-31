import { Stack, Text } from '@primer/react'

import './StatCard.css'

/**
 * One number with its label.
 *
 * Lifted out of the Inference page when the group overview needed the
 * same thing. Every card carries a sub-line slot even when empty, so a
 * row of them stays the same height — the version without one sat
 * visibly shorter than its neighbours.
 */
export function StatCard({
  label,
  value,
  sub,
  tone,
}: {
  label: string
  value: string
  sub?: string
  tone?: 'bad'
}) {
  return (
    <Stack className="stat-card" padding="normal" gap="condensed">
      <Text size="small" className="stat-card-muted">
        {label}
      </Text>
      {/* Text, not Heading. A stat card is a label and a number; the
          number was `<Heading as="h3">`, which put "0" and "56
          concurrent" in the document outline and made `/inference` jump
          from the page's h1 straight to an h3. axe's heading-order,
          and a screen reader announcing a figure as a section. F137.
          The weight moved to the stylesheet, which Primer's Heading
          was supplying. */}
      <Text className={`stat-card-value${tone === 'bad' ? ' stat-card-value--bad' : ''}`}>
        {value}
      </Text>
      {sub && (
        <Text size="small" className="stat-card-muted">
          {sub}
        </Text>
      )}
    </Stack>
  )
}
