import { Text } from '@primer/react'

/** One fact, as a term and its description. */
export function Fact({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <>
      <Text as="dt" size="small" className="inference-muted">
        {term}
      </Text>
      <dd className="inference-fact-value">{children}</dd>
    </>
  )
}

/** A value followed by a muted gloss, as most facts read. */
export function Glossed({ value, gloss }: { value: React.ReactNode; gloss?: React.ReactNode }) {
  return (
    <>
      <Text size="small">{value}</Text>
      {gloss && (
        <Text size="small" className="inference-muted">
          {' '}
          {gloss}
        </Text>
      )}
    </>
  )
}
