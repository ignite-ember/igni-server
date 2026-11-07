import { Heading, Link, Stack, Text } from '@primer/react'
import { AlertIcon, CheckCircleIcon, StopIcon } from '@primer/octicons-react'
import { Link as RouterLink } from 'react-router-dom'

import type { AttentionItem } from './types'

/**
 * What needs someone's eye, first on the page — or, when nothing does,
 * one line saying so, so an empty panel is never mistaken for one that
 * failed to load.
 */
export function AttentionPanel({ items, ready }: { items: AttentionItem[]; ready: boolean }) {
  if (!ready) return null
  if (items.length === 0) {
    return (
      <Stack direction="horizontal" align="center" gap="condensed" className="dashboard-all-clear">
        <CheckCircleIcon className="dashboard-good" />
        <Text size="small">Nothing needs your attention.</Text>
      </Stack>
    )
  }
  return (
    <section className="dashboard-attention" aria-labelledby="dashboard-attention-heading">
      <Heading as="h2" variant="small" id="dashboard-attention-heading">
        Needs attention
      </Heading>
      <ul className="dashboard-attention-list">
        {items.map((item) => (
          <li key={item.id} className={`dashboard-attention-item dashboard-attention-item--${item.tone}`}>
            {item.tone === 'danger' ? (
              <StopIcon className="dashboard-bad" aria-label="Urgent" />
            ) : (
              <AlertIcon className="dashboard-warn" aria-label="Worth a look" />
            )}
            <Stack gap="none" className="dashboard-attention-text">
              <Link as={RouterLink} to={item.to} className="dashboard-attention-title">
                {item.title}
              </Link>
              <Text size="small" className="dashboard-muted dashboard-attention-detail" title={item.detail}>
                {item.detail}
              </Text>
            </Stack>
          </li>
        ))}
      </ul>
    </section>
  )
}
