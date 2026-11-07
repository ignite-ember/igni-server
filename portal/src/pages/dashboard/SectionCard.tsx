import type { ReactNode } from 'react'
import { Heading, Link, SkeletonBox, Stack, Text } from '@primer/react'
import { Link as RouterLink } from 'react-router-dom'

/**
 * One area of igni on the dashboard: its name (which goes to its page),
 * the few facts that matter, and a way through to everything.
 *
 * ``loading`` and ``failed`` render the card's frame anyway, so the grid
 * keeps its shape while the rest of the dashboard fills in.
 */
export function SectionCard({
  title,
  to,
  linkLabel,
  loading,
  failed,
  children,
}: {
  title: string
  to: string
  /** The footer link's words: what the page shows, not "View all". */
  linkLabel: string
  loading?: boolean
  failed?: boolean
  children: ReactNode
}) {
  const headingId = `dashboard-section-${to.replace(/\W+/g, '-')}`
  return (
    <section className="dashboard-section" aria-labelledby={headingId}>
      <Stack gap="normal" className="dashboard-section-body">
        <Heading as="h2" variant="small" id={headingId}>
          <Link as={RouterLink} to={to} muted className="dashboard-section-title">
            {title}
          </Link>
        </Heading>
        {loading ? (
          <Stack gap="condensed">
            <SkeletonBox height={20} />
            <SkeletonBox height={20} />
            <SkeletonBox height={20} />
          </Stack>
        ) : failed ? (
          <Text size="small" className="dashboard-muted">
            Could not load this just now.
          </Text>
        ) : (
          children
        )}
      </Stack>
      <Link as={RouterLink} to={to} className="dashboard-section-more">
        {linkLabel} →
      </Link>
    </section>
  )
}

/** Two or three headline facts in a row: a number and what it counts. */
export function Facts({ facts }: { facts: Array<{ label: string; value: string; tone?: 'bad' }> }) {
  return (
    <dl className="dashboard-facts">
      {facts.map((f) => (
        <div key={f.label} className="dashboard-fact">
          <dt className="dashboard-muted">{f.label}</dt>
          <dd className={f.tone === 'bad' ? 'dashboard-fact-value dashboard-bad' : 'dashboard-fact-value'}>
            {f.value}
          </dd>
        </div>
      ))}
    </dl>
  )
}

/** A short list of an area's latest or largest things. */
export function Rows({ children, empty }: { children: ReactNode[]; empty: string }) {
  if (children.length === 0) {
    return (
      <Text size="small" className="dashboard-muted">
        {empty}
      </Text>
    )
  }
  return <ul className="dashboard-rows">{children}</ul>
}
